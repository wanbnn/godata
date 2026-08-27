from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import ConfigurationError, Settings
from .gateway import InvalidTargetError, SqlServerError, SqlServerGateway, TargetNotAllowedError
from .models import HealthResponse, QueryRequest, QueryResponse
from .sql_validation import UnsafeQueryError, validate_read_only_query

logger = logging.getLogger("godata")
api_key_header = APIKeyHeader(name="X-API-Key", scheme_name="GoDataApiKey")


def create_app(settings: Settings | None = None, gateway: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_settings = settings or Settings.from_env()
        application.state.settings = active_settings
        application.state.gateway = gateway or SqlServerGateway(active_settings)
        application.state.query_slots = asyncio.Semaphore(active_settings.max_concurrent_queries)
        yield

    application = FastAPI(
        title="GoData",
        version=__version__,
        description="Proxy HTTP somente-leitura para SQL Server com autenticação integrada do Windows.",
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

    @application.post(
        "/v1/query",
        response_model=QueryResponse,
        dependencies=[Depends(require_api_key)],
        tags=["query"],
    )
    async def query(body: QueryRequest, request: Request) -> QueryResponse:
        try:
            validate_read_only_query(body.query, request.app.state.settings.max_query_length)
            async with request.app.state.query_slots:
                result = await run_in_threadpool(
                    request.app.state.gateway.execute,
                    body.server,
                    body.database,
                    body.query,
                    body.parameters,
                )
        except (UnsafeQueryError, InvalidTargetError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TargetNotAllowedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SqlServerError as exc:
            logger.exception("Falha SQL Server; request_id=%s", request.state.request_id)
            raise HTTPException(status_code=502, detail="Falha ao consultar o SQL Server") from exc

        return QueryResponse(
            request_id=request.state.request_id,
            columns=result.columns,
            rows=result.rows,
            row_count=len(result.rows),
            truncated=result.truncated,
            elapsed_ms=result.elapsed_ms,
        )

    @application.exception_handler(ConfigurationError)
    async def configuration_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return application


app = create_app()
