import json
from types import SimpleNamespace

from godata import create_engine


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_engine_sends_request_and_maps_result(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append({
            "url": request.full_url,
            "method": request.method,
            "headers": request.headers,
            "payload": json.loads(request.data) if request.data else None,
        })
        assert timeout == 15
        if request.full_url.endswith("/v1/queries"):
            return FakeResponse({
                "query_id": "job-123", "request_id": "abc-123", "status": "running",
            })
        if request.full_url.endswith("/v1/queries/job-123/result"):
            return FakeResponse({
                "request_id": "abc-123", "columns": ["id", "nome"], "rows": [[7, "Alice"]],
                "row_count": 1, "rows_affected": 0, "truncated": False, "elapsed_ms": 3,
            })
        return FakeResponse({
            "query_id": "job-123", "request_id": "abc-123", "status": "completed",
        })

    monkeypatch.setattr("godata.client.urlopen", fake_urlopen)
    engine = create_engine(
        "https://godata.example/", "api-key", "sql01", "ERP", timeout=15, poll_interval=0,
    )

    result = engine.query("SELECT id, nome FROM clientes WHERE id = ?", [7])

    assert [call["url"] for call in captured] == [
        "https://godata.example/v1/queries",
        "https://godata.example/v1/queries/job-123",
        "https://godata.example/v1/queries/job-123/result",
    ]
    assert captured[0]["headers"]["X-api-key"] == "api-key"
    assert captured[0]["headers"]["Idempotency-key"]
    assert captured[0]["payload"] == {
        "server": "sql01", "database": "ERP", "query": "SELECT id, nome FROM clientes WHERE id = ?", "parameters": [7],
    }
    assert result.first() == [7, "Alice"]
    assert list(result.mappings()) == [{"id": 7, "nome": "Alice"}]
