from unittest.mock import MagicMock

import pytest

from orchestrator.polygon import client as polygon_client
from orchestrator.polygon.client import PolygonApiError, PolygonClient


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    # Тесты не должны зависеть от реального .env в корне репозитория.
    monkeypatch.setattr(polygon_client, "load_dotenv", lambda: None)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.setenv("POLYGON_API_SECRET", "test-secret")
    monkeypatch.delenv("POLYGON_API_BASE_URL", raising=False)


def _response(body=None, *, status_code: int = 200, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if body is None:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = body
    return response


def test_missing_credentials_raise_runtime_error(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="POLYGON_API_KEY/POLYGON_API_SECRET"):
        PolygonClient().call("problem.info", {})


def test_missing_secret_alone_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.delenv("POLYGON_API_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="POLYGON_API_KEY/POLYGON_API_SECRET"):
        PolygonClient().call("problem.info", {})


def test_call_signs_request_and_returns_result(credentials, monkeypatch):
    post = MagicMock(return_value=_response({"status": "OK", "result": {"id": 5}}))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    result = PolygonClient().call("problem.info", {"problemId": "5"})

    assert result == {"id": 5}
    args, kwargs = post.call_args
    assert args == ("https://polygon.codeforces.com/api/problem.info",)
    sent = kwargs["data"]
    assert sent["problemId"] == "5"
    assert sent["apiKey"] == "test-key"
    assert sent["time"].isdigit()
    # apiSig = rand (6 символов) + sha512 hex (128 символов)
    assert len(sent["apiSig"]) == 6 + 128
    assert kwargs["files"] is None


def test_call_uses_base_url_from_env_and_passes_files(credentials, monkeypatch):
    monkeypatch.setenv("POLYGON_API_BASE_URL", "https://example.test/api/")
    post = MagicMock(return_value=_response({"status": "OK", "result": None}))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    files = {"file": ("gen.cpp", b"int main() {}")}
    PolygonClient().call("problem.saveFile", {"problemId": "5"}, files=files)

    args, kwargs = post.call_args
    assert args == ("https://example.test/api/problem.saveFile",)
    assert kwargs["files"] == files


def test_failed_status_raises_polygon_api_error(credentials, monkeypatch):
    post = MagicMock(
        return_value=_response({"status": "FAILED", "comment": "problemId: Incorrect"})
    )
    monkeypatch.setattr(polygon_client.requests, "post", post)

    with pytest.raises(PolygonApiError) as exc_info:
        PolygonClient().call("problem.info", {"problemId": "x"})

    assert exc_info.value.method_name == "problem.info"
    assert exc_info.value.comment == "problemId: Incorrect"
    assert str(exc_info.value) == "Polygon API problem.info: problemId: Incorrect"


def test_non_json_response_raises_polygon_api_error(credentials, monkeypatch):
    post = MagicMock(return_value=_response(None, status_code=502, text="Bad Gateway"))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    with pytest.raises(PolygonApiError, match="HTTP 502"):
        PolygonClient().call("problem.info", {})
