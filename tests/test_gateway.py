import sys
import threading
from types import SimpleNamespace

import pytest

from godata.config import Settings
from godata.gateway import QueryCancelledError, QueryTimeoutError, SqlServerGateway


class FakeCursor:
    description = [("value",)]
    rowcount = -1

    def execute(self, query, parameters):
        assert query == "SELECT ?"
        assert parameters == (7,)

    def fetchall(self):
        return [(7,)]


class FakeConnection:
    def __init__(self):
        self.timeout = 0
        self.rolled_back = False
        self.committed = False
        self.closed = False

    def cursor(self):
        assert self.timeout == 42
        return FakeCursor()

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_query_timeout_is_set_on_connection_before_cursor(monkeypatch):
    connection = FakeConnection()
    fake_pyodbc = SimpleNamespace(
        Error=Exception,
        connect=lambda *args, **kwargs: connection,
    )
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    settings = Settings(api_key="a" * 32, query_timeout_seconds=42)

    result = SqlServerGateway(settings).execute("sql01", "ERP", "SELECT ?", [7])

    assert result.rows == [[7]]
    assert result.rows_affected == 0
    assert connection.committed
    assert connection.rolled_back
    assert connection.closed


def test_odbc_timeout_is_classified(monkeypatch):
    class FakeOdbcError(Exception):
        pass

    def raise_timeout(*args, **kwargs):
        raise FakeOdbcError("HYT00", "query timeout expired")

    monkeypatch.setitem(
        sys.modules,
        "pyodbc",
        SimpleNamespace(Error=FakeOdbcError, connect=raise_timeout),
    )

    with pytest.raises(QueryTimeoutError):
        SqlServerGateway(Settings(api_key="a" * 32)).execute("sql01", "ERP", "SELECT 1", [])


def test_non_result_query_is_committed_and_reports_affected_rows(monkeypatch):
    class WriteCursor:
        description = None
        rowcount = 3

        def execute(self, query, parameters):
            assert query == "DELETE FROM clientes"
            assert parameters == ()

        def nextset(self):
            return False

    class WriteConnection(FakeConnection):
        def cursor(self):
            return WriteCursor()

    connection = WriteConnection()
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(Error=Exception, connect=lambda *args, **kwargs: connection))

    result = SqlServerGateway(Settings(api_key="a" * 32)).execute("sql01", "ERP", "DELETE FROM clientes", [])

    assert result.columns == []
    assert result.rows == []
    assert result.rows_affected == 3
    assert connection.committed


def test_running_job_can_be_cancelled(monkeypatch):
    class FakeOdbcError(Exception):
        pass

    class CancellableCursor:
        description = [("value",)]

        def __init__(self):
            self.started = threading.Event()
            self.cancelled = threading.Event()

        def execute(self, query, parameters):
            self.started.set()
            assert self.cancelled.wait(timeout=1)
            raise FakeOdbcError("HY008", "operation cancelled")

        def cancel(self):
            self.cancelled.set()

    cursor = CancellableCursor()

    class CancellableConnection(FakeConnection):
        def cursor(self):
            return cursor

    gateway = SqlServerGateway(Settings(api_key="a" * 32))
    monkeypatch.setitem(
        sys.modules,
        "pyodbc",
        SimpleNamespace(Error=FakeOdbcError, connect=lambda *args, **kwargs: CancellableConnection()),
    )
    errors = []

    def execute():
        try:
            gateway.execute_job("job-1", "sql01", "ERP", "WAITFOR", [])
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    assert cursor.started.wait(timeout=1)
    assert gateway.cancel("job-1") is True
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], QueryCancelledError)
