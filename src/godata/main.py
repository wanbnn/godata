from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import ConfigurationError, Settings
from .gateway import InvalidTargetError, QueryTimeoutError, SqlServerError, SqlServerGateway
from .jobs import (
    TERMINAL_STATUSES,
    IdempotencyConflictError,
    JobNotFoundError,
    QueryJobManager,
    encode_sse,
)
from .models import (
    ColumnInfo,
    DatabaseInfo,
    HealthResponse,
    QueryJobResponse,
    QueryRequest,
    QueryResponse,
    SchemaInfo,
    TableInfo,
)

logger = logging.getLogger("godata")
api_key_header = APIKeyHeader(name="X-API-Key", scheme_name="GoDataApiKey")


def create_app(settings: Settings | None = None, gateway: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_settings = settings or Settings.from_env()
        application.state.settings = active_settings
        application.state.gateway = gateway or SqlServerGateway(active_settings)
        application.state.query_slots = asyncio.Semaphore(active_settings.max_concurrent_queries)
        application.state.query_jobs = QueryJobManager(
            application.state.gateway,
            max_workers=active_settings.max_concurrent_queries,
            ttl_seconds=active_settings.query_job_ttl_seconds,
        )
        try:
            yield
        finally:
            application.state.query_jobs.shutdown()

    application = FastAPI(
        title="GoData",
        version=__version__,
        description="Proxy HTTP para SQL Server com autenticação integrada do Windows.",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    def require_api_key(request: Request, supplied: Annotated[str, Depends(api_key_header)]) -> None:
        expected = request.app.state.settings.api_key
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key inválida")

    @application.get("/health", response_model=HealthResponse, tags=["infra"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="godata", version=__version__)

    async def run_discovery(request: Request, method: str, *args: str | None):
        try:
            async with request.app.state.query_slots:
                return await run_in_threadpool(getattr(request.app.state.gateway, method), *args)
        except InvalidTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except QueryTimeoutError as exc:
            logger.warning("Timeout no discovery SQL Server; request_id=%s", request.state.request_id)
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except SqlServerError as exc:
            logger.exception("Falha no discovery SQL Server; request_id=%s", request.state.request_id)
            raise HTTPException(status_code=502, detail="Falha ao consultar metadados do SQL Server") from exc

    @application.get("/v1/discovery/databases", response_model=list[DatabaseInfo], dependencies=[Depends(require_api_key)], tags=["discovery"])
    async def databases(server: str, request: Request):
        return await run_discovery(request, "list_databases", server)

    @application.get("/v1/discovery/schemas", response_model=list[SchemaInfo], dependencies=[Depends(require_api_key)], tags=["discovery"])
    async def schemas(server: str, database: str, request: Request):
        return await run_discovery(request, "list_schemas", server, database)

    @application.get("/v1/discovery/tables", response_model=list[TableInfo], dependencies=[Depends(require_api_key)], tags=["discovery"])
    async def tables(server: str, database: str, request: Request, schema: str | None = None):
        return await run_discovery(request, "list_tables", server, database, schema)

    @application.get("/v1/discovery/columns", response_model=list[ColumnInfo], dependencies=[Depends(require_api_key)], tags=["discovery"])
    async def columns(server: str, database: str, schema: str, table: str, request: Request):
        return await run_discovery(request, "list_columns", server, database, schema, table)

    @application.post(
        "/v1/query",
        response_model=QueryResponse,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def query(body: QueryRequest, request: Request) -> QueryResponse:
        try:
            async with request.app.state.query_slots:
                result = await run_in_threadpool(
                    request.app.state.gateway.execute,
                    body.server,
                    body.database,
                    body.query,
                    body.parameters,
                )
        except InvalidTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except QueryTimeoutError as exc:
            logger.warning("Timeout SQL Server; request_id=%s", request.state.request_id)
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except SqlServerError as exc:
            logger.exception("Falha SQL Server; request_id=%s", request.state.request_id)
            raise HTTPException(status_code=502, detail="Falha ao consultar o SQL Server") from exc

        return QueryResponse(
            request_id=request.state.request_id,
            columns=result.columns,
            rows=result.rows,
            row_count=len(result.rows),
            rows_affected=result.rows_affected,
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
        )

    def query_jobs(request: Request) -> QueryJobManager:
        return request.app.state.query_jobs

    def get_job(request: Request, query_id: str) -> dict[str, Any]:
        try:
            return query_jobs(request).get(query_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Consulta não encontrada ou expirada") from exc

    @application.post(
        "/v1/queries",
        response_model=QueryJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def submit_query(body: QueryRequest, request: Request, response: Response) -> dict[str, Any]:
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key is not None and len(idempotency_key) > 255:
            raise HTTPException(status_code=400, detail="Idempotency-Key deve possuir no máximo 255 caracteres")
        try:
            job = query_jobs(request).submit(
                request.state.request_id,
                body,
                idempotency_key,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response.headers["Location"] = f"/v1/queries/{job['query_id']}"
        return job

    @application.get(
        "/v1/queries/{query_id}",
        response_model=QueryJobResponse,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def query_status(query_id: str, request: Request) -> dict[str, Any]:
        return get_job(request, query_id)

    @application.get(
        "/v1/queries/{query_id}/result",
        response_model=QueryResponse,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def query_result(query_id: str, request: Request) -> QueryResponse:
        try:
            job, result = query_jobs(request).result(query_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Consulta não encontrada ou expirada") from exc
        if job["status"] != "completed" or result is None:
            detail = job["error"] or f"Consulta ainda está {job['status']}"
            headers = {"Retry-After": "2"} if job["status"] in {"queued", "running"} else None
            raise HTTPException(status_code=409, detail=detail, headers=headers)
        return QueryResponse(
            request_id=job["request_id"],
            columns=result.columns,
            rows=result.rows,
            row_count=len(result.rows),
            rows_affected=result.rows_affected,
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
        )

    @application.delete(
        "/v1/queries/{query_id}",
        response_model=QueryJobResponse,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def cancel_query(query_id: str, request: Request) -> dict[str, Any]:
        try:
            return query_jobs(request).cancel(query_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Consulta não encontrada ou expirada") from exc

    @application.get(
        "/v1/queries/{query_id}/events",
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def query_events(query_id: str, request: Request) -> StreamingResponse:
        initial = get_job(request, query_id)
        heartbeat = request.app.state.settings.sse_heartbeat_seconds

        async def events():
            current = initial
            yield encode_sse("status", current)
            while current["status"] not in TERMINAL_STATUSES:
                if await request.is_disconnected():
                    return
                current = await asyncio.to_thread(
                    query_jobs(request).wait_for_change,
                    query_id,
                    current["version"],
                    heartbeat,
                )
                if current.pop("changed"):
                    yield encode_sse("status", current)
                else:
                    yield f": heartbeat {int(asyncio.get_running_loop().time())}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.exception_handler(ConfigurationError)
    async def configuration_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return application


app = create_app()
