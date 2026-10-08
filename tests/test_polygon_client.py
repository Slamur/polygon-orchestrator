from unittest.mock import MagicMock

import pytest

from orchestrator.polygon import client as polygon_client
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.signing import generate_signature


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
    # Все параметры — поля multipart-формы без имени файла.
    sent = {name: value for name, (filename, value) in kwargs["files"].items()}
    assert all(filename is None for filename, _ in kwargs["files"].values())
    assert sent["problemId"] == "5"
    assert sent["apiKey"] == "test-key"
    assert sent["time"].isdigit()
    # apiSig = rand (6 символов) + sha512 hex (128 символов)
    assert len(sent["apiSig"]) == 6 + 128


def test_call_uses_base_url_from_env(credentials, monkeypatch):
    monkeypatch.setenv("POLYGON_API_BASE_URL", "https://example.test/api/")
    post = MagicMock(return_value=_response({"status": "OK", "result": None}))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    PolygonClient().call("problem.info", {"problemId": "5"})

    args, _ = post.call_args
    assert args == ("https://example.test/api/problem.info",)


def test_file_content_is_a_signed_param(credentials, monkeypatch):
    # Polygon подписывает `file` наравне с остальными параметрами: контент
    # должен уйти полем формы и войти в apiSig, а не отдельной частью вне подписи.
    post = MagicMock(return_value=_response({"status": "OK", "result": None}))
    monkeypatch.setattr(polygon_client.requests, "post", post)
    monkeypatch.setattr(polygon_client.time, "time", lambda: 1700000000)
    content = b"#include <cstdio>\nint main() { return 0; }\n"

    PolygonClient().call("problem.saveFile", {"problemId": "5", "name": "a.cpp", "file": content})

    sent = {name: value for name, (_, value) in post.call_args.kwargs["files"].items()}
    assert sent["file"] == content
    rand = sent["apiSig"][:6]
    unsigned = {name: value for name, value in sent.items() if name != "apiSig"}
    assert sent["apiSig"] == generate_signature(
        "problem.saveFile", unsigned, "test-secret", rand=rand
    )
    assert generate_signature(
        "problem.saveFile",
        {**unsigned, "file": b"other"},
        "test-secret",
        rand=rand,
    ) != sent["apiSig"]


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


def _raw_response(content: bytes, *, status_code: int = 200, content_type: str = "text/plain", body=None):
    response = _response(body, status_code=status_code, text=content.decode("utf-8", "replace"))
    response.content = content
    response.headers = {"Content-Type": content_type}
    return response


def test_call_raw_returns_body_as_is(credentials, monkeypatch):
    # Скачиваемый файл сам может быть JSON — тело не разбирается.
    content = b'{"status": "FAILED"}\r\n'
    post = MagicMock(return_value=_raw_response(content))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    result = PolygonClient().call_raw("problem.viewFile", {"problemId": "5", "name": "a.json"})

    assert result == content
    assert post.call_args.args == ("https://polygon.codeforces.com/api/problem.viewFile",)


@pytest.mark.parametrize("status_code", [200, 400])
def test_call_raw_failed_json_raises_polygon_api_error(credentials, monkeypatch, status_code):
    body = {"status": "FAILED", "comment": "name: File not found"}
    post = MagicMock(
        return_value=_raw_response(
            b"{}", status_code=status_code, content_type="application/json", body=body
        )
    )
    monkeypatch.setattr(polygon_client.requests, "post", post)

    with pytest.raises(PolygonApiError) as exc_info:
        PolygonClient().call_raw("problem.viewFile", {"problemId": "5", "name": "x"})

    assert exc_info.value.comment == "name: File not found"


def test_call_raw_non_200_without_json_raises_polygon_api_error(credentials, monkeypatch):
    post = MagicMock(return_value=_raw_response(b"Bad Gateway", status_code=502))
    monkeypatch.setattr(polygon_client.requests, "post", post)

    with pytest.raises(PolygonApiError, match="HTTP 502"):
        PolygonClient().call_raw("problem.script", {"problemId": "5", "testset": "tests"})
