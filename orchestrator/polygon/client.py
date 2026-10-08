"""Тонкий HTTP-клиент поверх Polygon API."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from dotenv import load_dotenv

from orchestrator.polygon.signing import generate_signature

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://polygon.codeforces.com/api"
_REQUEST_TIMEOUT_SECONDS = 60


class PolygonApiError(Exception):
    """`comment` из ответа Polygon при `status == "FAILED"`."""

    def __init__(self, method_name: str, comment: str):
        self.method_name = method_name
        self.comment = comment
        super().__init__(f"Polygon API {method_name}: {comment}")


class PolygonClient:
    """Тонкий HTTP-клиент поверх Polygon API.

    Ключи читаются лениво, при первом вызове (см. `.env.example`) — тот же
    подход, что в `AnthropicClient._get_sdk_client`.
    """

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._api_secret: str | None = None
        self._base_url: str | None = None
        self._env_loaded = False

    def _ensure_credentials(self) -> tuple[str, str, str]:
        """Возвращает (api_key, api_secret, base_url), читая их из окружения.

        Если ключа/секрета нет — падаем сразу с понятной ошибкой, а не
        получаем невнятный FAILED от Polygon. `POLYGON_API_BASE_URL`
        необязателен (по умолчанию — боевой Polygon).
        """
        if self._api_key is None or self._api_secret is None or self._base_url is None:
            if not self._env_loaded:
                load_dotenv()
                self._env_loaded = True
            api_key = os.environ.get("POLYGON_API_KEY")
            api_secret = os.environ.get("POLYGON_API_SECRET")
            if not api_key or not api_secret:
                raise RuntimeError(
                    "POLYGON_API_KEY/POLYGON_API_SECRET are not set. Copy "
                    ".env.example to .env and put the keys there, or "
                    "export the environment variables directly."
                )
            self._api_key = api_key
            self._api_secret = api_secret
            self._base_url = os.environ.get("POLYGON_API_BASE_URL") or _DEFAULT_BASE_URL
        return self._api_key, self._api_secret, self._base_url

    def call(self, method_name: str, params: dict[str, str | bytes]) -> Any:
        """Вызывает один метод Polygon API, возвращает `result` или бросает `PolygonApiError`.

        Содержимое файлов (`file` в `problem.saveFile`/`problem.saveSolution`)
        передаётся в `params` наравне с остальными параметрами — как `bytes`
        или `str` — и входит в подпись (см. `generate_signature`). Запрос
        уходит как multipart/form-data, где каждый параметр — обычное поле
        формы без имени файла: так `bytes` передаются без перекодирования.
        """
        response = self._post(method_name, params)

        try:
            body = response.json()
        except ValueError:
            raise PolygonApiError(
                method_name,
                f"response is not JSON (HTTP {response.status_code}): "
                f"{response.text[:200]!r}",
            ) from None

        if body.get("status") == "FAILED":
            raise PolygonApiError(method_name, body.get("comment", ""))
        return body.get("result")

    def call_raw(self, method_name: str, params: dict[str, str | bytes]) -> bytes:
        """Вызывает метод Polygon API, который при успехе отдаёт не JSON, а само
        содержимое (`problem.viewFile`, `problem.viewSolution`,
        `problem.script`, `problem.testInput`) — возвращает тело ответа как
        есть.

        Ошибка у таких методов приходит обычным JSON со `status == "FAILED"`.
        Она распознаётся по не-200 HTTP-статусу; на случай, если Polygon
        отдаст FAILED с HTTP 200, — ещё и по `Content-Type: application/json`
        (по одному телу судить нельзя: скачиваемый файл сам может быть JSON).

        NOTE: на реальном API не проверено, каким HTTP-статусом и
        Content-Type Polygon сопровождает FAILED у этих методов.
        """
        response = self._post(method_name, params)

        content_type = response.headers.get("Content-Type", "")
        if response.status_code != 200 or "application/json" in content_type:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("status") == "FAILED":
                raise PolygonApiError(method_name, body.get("comment", ""))
            if response.status_code != 200:
                raise PolygonApiError(
                    method_name,
                    f"unexpected response (HTTP {response.status_code}): "
                    f"{response.text[:200]!r}",
                )
        return response.content

    def _post(self, method_name: str, params: dict[str, str | bytes]) -> requests.Response:
        """Подписывает и отправляет один запрос; разбор ответа — у вызывающего."""
        api_key, api_secret, base_url = self._ensure_credentials()

        signed_params: dict[str, str | bytes] = {
            **params,
            "apiKey": api_key,
            "time": str(int(time.time())),
        }
        signed_params["apiSig"] = generate_signature(method_name, signed_params, api_secret)

        url = f"{base_url.rstrip('/')}/{method_name}"
        logger.info("PolygonClient.call(%s): POST %s", method_name, url)
        return requests.post(
            url,
            files={name: (None, value) for name, value in signed_params.items()},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
