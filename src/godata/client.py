"""Cliente Python nativo para a API HTTP do GoData."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GoDataError(RuntimeError):
    """Erro ao se comunicar com o serviço GoData."""


class GoDataHTTPError(GoDataError):
    """Resposta HTTP de erro retornada pelo serviço GoData."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"GoData retornou HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Resultado de uma execução SQL."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    rows_affected: int | None
    elapsed_ms: int
    request_id: str

    def mappings(self) -> Iterator[dict[str, Any]]:
        """Itera as linhas como dicionários, no estilo de resultados SQLAlchemy."""
        for row in self.rows:
            yield dict(zip(self.columns, row, strict=True))

    def all(self) -> list[list[Any]]:
        """Retorna todas as linhas como vetores."""
        return self.rows

    def first(self) -> list[Any] | None:
        """Retorna a primeira linha, ou ``None`` se não houver resultado."""
        return self.rows[0] if self.rows else None


class Engine:
    """Conexão configurada para um servidor e banco SQL via GoData."""

    def __init__(self, url: str, api_key: str, server: str, database: str, *, timeout: float | None = None):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.server = server
        self.database = database
        self.timeout = timeout

    def query(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
        *,
        server: str | None = None,
        database: str | None = None,
    ) -> QueryResult:
        """Executa qualquer comando T-SQL e retorna seu resultado."""
        payload = {
            "server": server or self.server,
            "database": database or self.database,
            "query": sql,
            "parameters": list(parameters or []),
        }
        request = Request(
            f"{self.url}/v1/query",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _error_detail(exc)
            raise GoDataHTTPError(exc.code, detail) from exc
        except URLError as exc:
            raise GoDataError(f"Não foi possível conectar ao GoData: {exc.reason}") from exc

        return QueryResult(
            columns=body["columns"],
            rows=body["rows"],
            row_count=body["row_count"],
            rows_affected=body.get("rows_affected"),
            elapsed_ms=body["elapsed_ms"],
            request_id=body["request_id"],
        )

    execute = query


def create_engine(url: str, api_key: str, server: str, database: str, *, timeout: float | None = None) -> Engine:
    """Cria uma :class:`Engine` pronta para executar queries."""
    return Engine(url, api_key, server, database, timeout=timeout)


def _error_detail(error: HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("detail", body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error.reason
