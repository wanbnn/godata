"""Cliente Python nativo para a API HTTP do GoData."""

from __future__ import annotations

import json
import time
import uuid
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


class GoDataJobError(GoDataError):
    """A execução assíncrona terminou sem produzir um resultado."""

    def __init__(self, query_id: str, status: str, detail: str):
        super().__init__(f"Consulta {query_id} terminou como {status}: {detail}")
        self.query_id = query_id
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class QueryJob:
    """Referência recuperável para uma consulta executada pelo GoData."""

    query_id: str
    request_id: str
    status: str
    error: str | None = None


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

    def __init__(
        self,
        url: str,
        api_key: str,
        server: str,
        database: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 2.0,
        status_retries: int = 5,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.server = server
        self.database = database
        if poll_interval < 0:
            raise ValueError("poll_interval deve ser maior ou igual a zero")
        if status_retries < 0:
            raise ValueError("status_retries deve ser maior ou igual a zero")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.status_retries = status_retries

    def query(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
        *,
        server: str | None = None,
        database: str | None = None,
    ) -> QueryResult:
        """Executa T-SQL por meio de um job recuperável e retorna seu resultado."""
        job = self.submit_query(sql, parameters, server=server, database=database)
        retries = 0
        while job.status not in {"completed", "failed", "cancelled"}:
            time.sleep(self.poll_interval)
            try:
                job = self.get_query(job.query_id)
                retries = 0
            except GoDataHTTPError:
                raise
            except GoDataError as exc:
                retries += 1
                if retries > self.status_retries:
                    raise GoDataError(
                        f"Não foi possível acompanhar a consulta {job.query_id}; "
                        "ela pode continuar em execução no GoData"
                    ) from exc
        if job.status != "completed":
            raise GoDataJobError(job.query_id, job.status, job.error or "sem detalhes")
        return self.get_query_result(job.query_id)

    def submit_query(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
        *,
        server: str | None = None,
        database: str | None = None,
        idempotency_key: str | None = None,
    ) -> QueryJob:
        """Submete uma consulta e retorna imediatamente seu identificador."""
        payload = {
            "server": server or self.server,
            "database": database or self.database,
            "query": sql,
            "parameters": list(parameters or []),
        }
        body = self._request_json(
            "POST",
            "/v1/queries",
            payload,
            {"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )
        return _query_job(body)

    def get_query(self, query_id: str) -> QueryJob:
        """Consulta o estado atual de um job."""
        return _query_job(self._request_json("GET", f"/v1/queries/{query_id}"))

    def get_query_result(self, query_id: str) -> QueryResult:
        """Recupera o resultado de um job concluído."""
        body = self._request_json("GET", f"/v1/queries/{query_id}/result")
        return QueryResult(
            columns=body["columns"],
            rows=body["rows"],
            row_count=body["row_count"],
            rows_affected=body.get("rows_affected"),
            elapsed_ms=body["elapsed_ms"],
            request_id=body["request_id"],
        )

    def cancel_query(self, query_id: str) -> QueryJob:
        """Solicita o cancelamento de um job pendente ou em execução."""
        return _query_job(self._request_json("DELETE", f"/v1/queries/{query_id}"))

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "X-API-Key": self.api_key}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, default=str).encode("utf-8")
        headers.update(extra_headers or {})
        request = Request(
            f"{self.url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _error_detail(exc)
            raise GoDataHTTPError(exc.code, detail) from exc
        except URLError as exc:
            raise GoDataError(f"Não foi possível conectar ao GoData: {exc.reason}") from exc

        return body

    execute = query


def create_engine(
    url: str,
    api_key: str,
    server: str,
    database: str,
    *,
    timeout: float | None = None,
    poll_interval: float = 2.0,
    status_retries: int = 5,
) -> Engine:
    """Cria uma :class:`Engine` pronta para executar queries."""
    return Engine(
        url,
        api_key,
        server,
        database,
        timeout=timeout,
        poll_interval=poll_interval,
        status_retries=status_retries,
    )


def _query_job(body: dict[str, Any]) -> QueryJob:
    return QueryJob(
        query_id=body["query_id"],
        request_id=body["request_id"],
        status=body["status"],
        error=body.get("error"),
    )


def _error_detail(error: HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("detail", body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error.reason
