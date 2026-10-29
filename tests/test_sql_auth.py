import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from godata import create_engine
from godata.config import Settings
from godata.gateway import SqlServerGateway, QueryResult
from godata.main import create_app
from godata.models import QueryRequest


def test_connection_string_uses_sql_auth_and_escapes_credentials():
    gateway = SqlServerGateway(Settings(api_key="a" * 32))
    connection = gateway._connection_string("sql01", "ERP", "user;name", " p};Trusted_Connection=Yes; ")
    assert "UID={user;name};" in connection
    assert "PWD={ p}};Trusted_Connection=Yes; };" in connection
    assert connection.count("Trusted_Connection=Yes") == 1  # Only inside the quoted password.
    assert "Trusted_Connection=Yes;ApplicationIntent" in gateway._connection_string("sql01", "ERP")


@pytest.mark.parametrize("credentials", [{"username": "user"}, {"password": "secret"}, {"username": "", "password": "secret"}])
def test_credentials_must_be_supplied_together(credentials):
    with pytest.raises(ValidationError):
        QueryRequest(server="sql01", database="ERP", query="SELECT 1", **credentials)
    with pytest.raises(ValueError):
        create_engine("https://example.com", "key", "sql01", "ERP", **credentials)


def test_sdk_sends_credentials_only_when_submitting(monkeypatch):
    captured = []
    def request(self, method, path, payload=None, extra_headers=None):
        captured.append(payload)
        return {"query_id": "job", "request_id": "request", "status": "queued"}
    monkeypatch.setattr("godata.client.Engine._request_json", request)
    engine = create_engine("https://example.com", "key", "sql01", "ERP", username="user", password=" secret ")
    engine.submit_query("SELECT 1")
    engine.get_query("job")
    assert captured[0]["username"] == "user"
    assert captured[0]["password"] == " secret "
    assert captured[1] is None


def test_api_forwards_credentials_for_sync_and_async_queries():
    received = []
    class Gateway:
        def execute(self, server, database, query, parameters, **credentials):
            received.append(credentials)
            return QueryResult([], [], 0, False, 0)
        def execute_job(self, execution_id, *args, **kwargs):
            return self.execute(*args, **kwargs)
    payload = dict(server="sql01", database="ERP", query="SELECT 1", username="user", password=" secret ")
    with TestClient(create_app(Settings(api_key="a" * 32), Gateway())) as client:
        headers = {"X-API-Key": "a" * 32}
        assert client.post("/v1/query", json=payload, headers=headers).status_code == 200
        job = client.post("/v1/queries", json=payload, headers=headers).json()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = client.get(f"/v1/queries/{job['query_id']}", headers=headers)
            if response.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert response.json()["status"] == "completed"
        assert "secret" not in json.dumps(response.json())
    assert received == [{"username": "user", "password": " secret "}] * 2
