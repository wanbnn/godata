from __future__ import annotations

import base64
import datetime as dt
import decimal
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from .config import Settings


class TargetNotAllowedError(ValueError):
    """O destino não pertence à allowlist."""


class InvalidTargetError(ValueError):
    """Servidor ou banco possui formato inseguro."""


class SqlServerError(RuntimeError):
    """Falha controlada ao carregar o ODBC ou acessar o SQL Server."""


_SERVER_RE = re.compile(r"^[A-Za-z0-9_.\\,:-]+$")
_DATABASE_RE = re.compile(r"^[A-Za-z0-9_$#@. -]+$")


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


class SqlServerGateway:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _connection_string(self, server: str, database: str) -> str:
        server = server.strip()
        database = database.strip()
        if not _SERVER_RE.fullmatch(server) or not _DATABASE_RE.fullmatch(database):
            raise InvalidTargetError("Servidor ou banco possui caracteres inválidos")
        if not self.settings.target_is_allowed(server, database):
            raise TargetNotAllowedError("Servidor/banco não permitido")

        encrypt = "Yes" if self.settings.encrypt else "No"
        trust_certificate = "Yes" if self.settings.trust_server_certificate else "No"
        return (
            f"Driver={{{self.settings.odbc_driver}}};"
            f"Server={server};Database={database};"
            "Trusted_Connection=Yes;ApplicationIntent=ReadOnly;"
            f"Encrypt={encrypt};TrustServerCertificate={trust_certificate};"
        )

    def execute(self, server: str, database: str, query: str, parameters: Sequence[Any]) -> QueryResult:
        started = time.perf_counter()
        connection_string = self._connection_string(server, database)
        try:
            import pyodbc
        except ImportError as exc:
            raise SqlServerError("O runtime ODBC não está instalado") from exc

        try:
            connection = pyodbc.connect(
                connection_string,
                timeout=self.settings.connection_timeout_seconds,
                readonly=True,
                autocommit=False,
            )
            try:
                cursor = connection.cursor()
                cursor.timeout = self.settings.query_timeout_seconds
                cursor.execute(query, tuple(parameters))
                if cursor.description is None:
                    raise SqlServerError("A instrução não retornou um conjunto de resultados")

                columns = [column[0] for column in cursor.description]
                fetched = cursor.fetchmany(self.settings.max_rows + 1)
                truncated = len(fetched) > self.settings.max_rows
                rows = [[_serialize(value) for value in row] for row in fetched[: self.settings.max_rows]]
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    truncated=truncated,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
            finally:
                connection.rollback()
                connection.close()
        except pyodbc.Error as exc:
            raise SqlServerError("Falha no acesso ODBC ao SQL Server") from exc
