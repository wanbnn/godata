from fastapi.testclient import TestClient

from godata.config import Settings
from godata.gateway import QueryResult
from godata.main import create_app


class FakeGateway:
    def execute(self, server, database, query, parameters):
        assert (server, database) == ("sql01", "ERP")
        assert parameters == [7]
        return QueryResult(columns=["id", "nome"], rows=[[7, "Alice"]], truncated=False, elapsed_ms=3)


SETTINGS = Settings(
    api_key="a" * 32,
    allowed_targets={"sql01": frozenset({"erp"})},
)


def client() -> TestClient:
    return TestClient(create_app(SETTINGS, FakeGateway()))


def test_health_does_not_require_authentication():
    with client() as api:
        response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_query_requires_api_key():
    with client() as api:
        response = api.post(
            "/v1/query",
            json={"server": "sql01", "database": "ERP", "query": "SELECT 1"},
        )
    assert response.status_code in {401, 403}


def test_executes_parameterized_read_query():
    with client() as api:
        response = api.post(
            "/v1/query",
            headers={"X-API-Key": "a" * 32, "X-Request-ID": "test-123"},
            json={
                "server": "sql01",
                "database": "ERP",
                "query": "SELECT id, nome FROM dbo.clientes WHERE id = ?",
                "parameters": [7],
            },
        )
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-123",
        "columns": ["id", "nome"],
        "rows": [[7, "Alice"]],
        "row_count": 1,
        "truncated": False,
        "elapsed_ms": 3,
    }
    assert response.headers["X-Request-ID"] == "test-123"


def test_rejects_write_query_before_gateway():
    with client() as api:
        response = api.post(
            "/v1/query",
            headers={"X-API-Key": "a" * 32},
            json={"server": "sql01", "database": "ERP", "query": "DELETE FROM clientes"},
        )
    assert response.status_code == 400


def test_rejects_multiple_statements():
    with client() as api:
        response = api.post(
            "/v1/query",
            headers={"X-API-Key": "a" * 32},
            json={"server": "sql01", "database": "ERP", "query": "SELECT 1; SELECT 2"},
        )
    assert response.status_code == 400
