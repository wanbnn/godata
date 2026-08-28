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


class InvalidTargetError(ValueError):
    """Servidor ou banco possui formato inseguro."""


class SqlServerError(RuntimeError):
    """Falha controlada ao carregar o ODBC ou acessar o SQL Server."""


class QueryTimeoutError(SqlServerError):
    """A consulta excedeu o tempo limite configurado."""


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
                connection.timeout = self.settings.query_timeout_seconds
                cursor = connection.cursor()
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
            if exc.args and exc.args[0] in {"HYT00", "HYT01"}:
                raise QueryTimeoutError("O tempo limite da consulta expirou") from exc
            raise SqlServerError("Falha no acesso ODBC ao SQL Server") from exc

    def list_databases(self, server: str) -> list[dict[str, Any]]:
        result = self.execute(server, "master", """
            SELECT name FROM sys.databases
            WHERE state = 0 AND HAS_DBACCESS(name) = 1
            ORDER BY name
        """, [])
        return [{"name": row[0]} for row in result.rows]

    def list_schemas(self, server: str, database: str) -> list[dict[str, Any]]:
        result = self.execute(server, database, """
            SELECT name FROM sys.schemas
            WHERE name NOT IN ('sys', 'INFORMATION_SCHEMA')
            ORDER BY name
        """, [])
        return [{"name": row[0]} for row in result.rows]

    def list_tables(self, server: str, database: str, schema: str | None = None) -> list[dict[str, Any]]:
        result = self.execute(server, database, """
            SELECT s.name, o.name, CASE o.type WHEN 'U' THEN 'table' ELSE 'view' END
            FROM sys.objects AS o
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
              AND (? IS NULL OR s.name = ?)
            ORDER BY s.name, o.name
        """, [schema, schema])
        return [{"schema_name": row[0], "name": row[1], "type": row[2]} for row in result.rows]

    def list_columns(self, server: str, database: str, schema: str, table: str) -> list[dict[str, Any]]:
        result = self.execute(server, database, """
            SELECT s.name, o.name, c.name, c.column_id, t.name,
                   c.max_length, c.precision, c.scale, c.is_nullable
            FROM sys.columns AS c
            JOIN sys.objects AS o ON o.object_id = c.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            JOIN sys.types AS t ON t.user_type_id = c.user_type_id
            WHERE o.type IN ('U', 'V') AND s.name = ? AND o.name = ?
            ORDER BY c.column_id
        """, [schema, table])
        return [{
            "schema_name": row[0], "table_name": row[1], "name": row[2], "ordinal": row[3],
            "data_type": row[4], "max_length": row[5], "precision": row[6], "scale": row[7],
            "nullable": row[8],
        } for row in result.rows]
