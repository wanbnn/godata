import threading
import time

from godata.gateway import QueryResult
from godata.jobs import QueryJobManager
from godata.models import QueryRequest


class BlockingGateway:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, server, database, query, parameters):
        self.started.set()
        self.release.wait(timeout=2)
        return QueryResult(columns=[], rows=[], rows_affected=0, truncated=False, elapsed_ms=1)


def request(query="SELECT 1"):
    return QueryRequest(server="sql01", database="ERP", query=query)


def test_queued_job_can_be_cancelled_without_execution():
    gateway = BlockingGateway()
    manager = QueryJobManager(gateway, max_workers=1, ttl_seconds=60)
    try:
        first = manager.submit("request-1", request())
        assert gateway.started.wait(timeout=1)
        second = manager.submit("request-2", request("SELECT 2"))

        cancelled = manager.cancel(second["query_id"])

        assert cancelled["status"] == "cancelled"
        assert manager.get(first["query_id"])["status"] == "running"
    finally:
        gateway.release.set()
        deadline = time.monotonic() + 1
        while manager.get(first["query_id"])["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.01)
        manager.shutdown()
