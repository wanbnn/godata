from __future__ import annotations

import base64
import datetime as dt
import decimal
import re
import threading
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


class QueryCancelledError(SqlServerError):
    """A consulta foi cancelada explicitamente pelo cliente."""


_SERVER_RE = re.compile(r"^[A-Za-z0-9_.\\,:-]+$")
_DATABASE_RE = re.compile(r"^[A-Za-z0-9_$#@. -]+$")


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    rows_affected: int | None
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
        self._active_executions: set[str] = set()
        self._active_cursors: dict[str, Any] = {}
        self._cancelled: set[str] = set()
        self._cursor_lock = threading.Lock()
        self._query_slots = threading.BoundedSemaphore(settings.max_concurrent_queries)

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
            "Trusted_Connection=Yes;ApplicationIntent=ReadWrite;"
            f"Encrypt={encrypt};TrustServerCertificate={trust_certificate};"
        )

    def execute(self, server: str, database: str, query: str, parameters: Sequence[Any]) -> QueryResult:
        return self._execute(server, database, query, parameters, execution_id=None)

    def execute_job(
        self,
        execution_id: str,
        server: str,
        database: str,
        query: str,
        parameters: Sequence[Any],
    ) -> QueryResult:
        with self._cursor_lock:
            self._active_executions.add(execution_id)
        try:
            return self._execute(server, database, query, parameters, execution_id=execution_id)
        finally:
            with self._cursor_lock:
                self._active_executions.discard(execution_id)
                self._active_cursors.pop(execution_id, None)
                self._cancelled.discard(execution_id)

    def cancel(self, execution_id: str) -> bool:
        with self._cursor_lock:
            if execution_id not in self._active_executions:
                return False
            self._cancelled.add(execution_id)
            cursor = self._active_cursors.get(execution_id)
        if cursor is None:
            return False
        cursor.cancel()
        return True

    def _execute(
        self,
        server: str,
        database: str,
        query: str,
        parameters: Sequence[Any],
        execution_id: str | None,
    ) -> QueryResult:
        with self._query_slots:
            return self._execute_with_slot(server, database, query, parameters, execution_id)

    def _execute_with_slot(
        self,
        server: str,
        database: str,
        query: str,
        parameters: Sequence[Any],
        execution_id: str | None,
    ) -> QueryResult:
        started = time.perf_counter()
        cancelled_by_client = False
        connection_string = self._connection_string(server, database)
        try:
            import pyodbc
        except ImportError as exc:
            raise SqlServerError("O runtime ODBC não está instalado") from exc

        try:
            connection = pyodbc.connect(
                connection_string,
                timeout=self.settings.connection_timeout_seconds,
                readonly=False,
                autocommit=False,
            )
            try:
                connection.timeout = self.settings.query_timeout_seconds
                cursor = connection.cursor()
                if execution_id is not None:
                    with self._cursor_lock:
                        self._active_cursors[execution_id] = cursor
                        cancelled = execution_id in self._cancelled
                    if cancelled:
                        raise QueryCancelledError("A consulta foi cancelada")
                cursor.execute(query, tuple(parameters))
                rows_affected = 0
                while cursor.description is None:
                    if cursor.rowcount >= 0:
                        rows_affected += cursor.rowcount
                    if not cursor.nextset():
                        connection.commit()
                        return QueryResult(
                            columns=[],
                            rows=[],
                            rows_affected=rows_affected,
                            truncated=False,
                            elapsed_ms=round((time.perf_counter() - started) * 1000),
                        )

                columns = [column[0] for column in cursor.description]
                rows = [[_serialize(value) for value in row] for row in cursor.fetchall()]
                connection.commit()
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    rows_affected=rows_affected,
                    truncated=False,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
            finally:
                if execution_id is not None:
                    with self._cursor_lock:
                        cancelled_by_client = execution_id in self._cancelled
                        self._active_cursors.pop(execution_id, None)
                connection.rollback()
                connection.close()
        except pyodbc.Error as exc:
            if execution_id is not None:
                with self._cursor_lock:
                    cancelled_by_client = cancelled_by_client or execution_id in self._cancelled
            if cancelled_by_client:
                raise QueryCancelledError("A consulta foi cancelada") from exc
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
