import sys
from types import SimpleNamespace

import pytest

from godata.config import Settings
from godata.gateway import QueryTimeoutError, SqlServerGateway


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
