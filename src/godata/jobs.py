from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from .gateway import (
    InvalidTargetError,
    QueryCancelledError,
    QueryResult,
    QueryTimeoutError,
    SqlServerError,
)
from .models import QueryRequest

logger = logging.getLogger("godata")

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class JobNotFoundError(LookupError):
    pass


class IdempotencyConflictError(ValueError):
    pass


@dataclass(slots=True)
class QueryJob:
    query_id: str
    request_id: str
    body: QueryRequest
    fingerprint: str
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: QueryResult | None = None
    error: str | None = None
    cancel_requested: bool = False
    version: int = 0
    future: Future[None] | None = None


class QueryJobManager:
    """Executa queries fora do ciclo de vida da requisição HTTP."""

    def __init__(self, gateway: Any, max_workers: int, ttl_seconds: int):
        self._gateway = gateway
        self._ttl_seconds = ttl_seconds
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="godata-query")
        self._condition = threading.Condition()
        self._jobs: dict[str, QueryJob] = {}
        self._idempotency: dict[str, str] = {}

    def submit(self, request_id: str, body: QueryRequest, idempotency_key: str | None = None) -> dict[str, Any]:
        fingerprint = hashlib.sha256(body.model_dump_json().encode("utf-8")).hexdigest()
        with self._condition:
            self._purge_expired_locked()
            if idempotency_key and idempotency_key in self._idempotency:
                existing = self._jobs.get(self._idempotency[idempotency_key])
                if existing is not None:
                    if existing.fingerprint != fingerprint:
                        raise IdempotencyConflictError("Idempotency-Key já foi usada com outra consulta")
                    return self._snapshot_locked(existing)

            query_id = str(uuid.uuid4())
            job = QueryJob(query_id=query_id, request_id=request_id, body=body, fingerprint=fingerprint)
            self._jobs[query_id] = job
            if idempotency_key:
                self._idempotency[idempotency_key] = query_id
            job.future = self._executor.submit(self._run, query_id)
            return self._snapshot_locked(job)

    def get(self, query_id: str) -> dict[str, Any]:
        with self._condition:
            self._purge_expired_locked()
            return self._snapshot_locked(self._get_locked(query_id))

    def result(self, query_id: str) -> tuple[dict[str, Any], QueryResult | None]:
        with self._condition:
            self._purge_expired_locked()
            job = self._get_locked(query_id)
            return self._snapshot_locked(job), job.result

    def cancel(self, query_id: str) -> dict[str, Any]:
        with self._condition:
            self._purge_expired_locked()
            job = self._get_locked(query_id)
            if job.status in TERMINAL_STATUSES:
                return self._snapshot_locked(job)
            job.cancel_requested = True
            if job.status == "queued" and job.future is not None and job.future.cancel():
                job.status = "cancelled"
                job.finished_at = time.time()
                self._changed_locked(job)
                return self._snapshot_locked(job)

        cancel = getattr(self._gateway, "cancel", None)
        if cancel is not None:
            try:
                cancel(query_id)
            except Exception:
                logger.exception("Falha ao solicitar cancelamento; query_id=%s", query_id)
        return self.get(query_id)

    def wait_for_change(self, query_id: str, version: int, timeout: float) -> dict[str, Any]:
        with self._condition:
            job = self._get_locked(query_id)
            if job.version == version and job.status not in TERMINAL_STATUSES:
                self._condition.wait_for(
                    lambda: job.version != version or job.status in TERMINAL_STATUSES,
                    timeout=timeout,
                )
            snapshot = self._snapshot_locked(job)
            snapshot["changed"] = job.version != version
            return snapshot

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, query_id: str) -> None:
        with self._condition:
            job = self._jobs[query_id]
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = time.time()
                self._changed_locked(job)
                return
            job.status = "running"
            job.started_at = time.time()
            self._changed_locked(job)

        try:
            execute_job = getattr(self._gateway, "execute_job", None)
            if execute_job is not None:
                result = execute_job(
                    query_id,
                    job.body.server,
                    job.body.database,
                    job.body.query,
                    job.body.parameters,
                )
            else:
                result = self._gateway.execute(
                    job.body.server,
                    job.body.database,
                    job.body.query,
                    job.body.parameters,
                )
        except QueryCancelledError:
            self._finish(query_id, "cancelled")
        except InvalidTargetError as exc:
            self._finish(query_id, "failed", error=str(exc))
        except QueryTimeoutError as exc:
            self._finish(query_id, "failed", error=str(exc))
        except SqlServerError:
            logger.exception("Falha SQL Server; query_id=%s", query_id)
            self._finish(query_id, "failed", error="Falha ao consultar o SQL Server")
        except Exception:
            logger.exception("Falha inesperada no job; query_id=%s", query_id)
            self._finish(query_id, "failed", error="Falha interna ao executar a consulta")
        else:
            self._finish(query_id, "completed", result=result)

    def _finish(
        self,
        query_id: str,
        status: JobStatus,
        *,
        result: QueryResult | None = None,
        error: str | None = None,
    ) -> None:
        with self._condition:
            job = self._jobs[query_id]
            job.status = status
            job.result = result
            job.error = error
            job.finished_at = time.time()
            self._changed_locked(job)

    def _get_locked(self, query_id: str) -> QueryJob:
        try:
            return self._jobs[query_id]
        except KeyError as exc:
            raise JobNotFoundError(query_id) from exc

    def _changed_locked(self, job: QueryJob) -> None:
        job.version += 1
        self._condition.notify_all()

    def _snapshot_locked(self, job: QueryJob) -> dict[str, Any]:
        now = job.finished_at or time.time()
        start = job.started_at or job.created_at
        return {
            "query_id": job.query_id,
            "request_id": job.request_id,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "elapsed_ms": max(0, round((now - start) * 1000)),
            "error": job.error,
            "version": job.version,
        }

    def _purge_expired_locked(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]
        if expired:
            live_ids = set(self._jobs)
            self._idempotency = {
                key: job_id for key, job_id in self._idempotency.items() if job_id in live_ids
            }


def encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
