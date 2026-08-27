import sys
from types import SimpleNamespace

from godata.config import Settings
from godata.gateway import SqlServerGateway


class FakeCursor:
    description = [("value",)]

    def execute(self, query, parameters):
        assert query == "SELECT ?"
        assert parameters == (7,)

    def fetchmany(self, size):
        return [(7,)]


class FakeConnection:
    def __init__(self):
        self.timeout = 0
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        assert self.timeout == 42
        return FakeCursor()

    def rollback(self):
        self.rolled_back = True

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
    assert connection.rolled_back
    assert connection.closed
