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
                    "POLYGON_API_KEY/POLYGON_API_SECRET не заданы. Скопируйте "
                    ".env.example в .env и впишите туда ключи, либо "
                    "экспортируйте переменные окружения напрямую."
                )
            self._api_key = api_key
            self._api_secret = api_secret
            self._base_url = os.environ.get("POLYGON_API_BASE_URL") or _DEFAULT_BASE_URL
        return self._api_key, self._api_secret, self._base_url

    def call(
        self,
        method_name: str,
        params: dict[str, str],
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> Any:
        """Вызывает один метод Polygon API, возвращает `result` или бросает `PolygonApiError`.

        `files` — `{имя поля: (имя файла, содержимое)}`; при их наличии запрос
        уходит как multipart/form-data, иначе — обычной формой. Содержимое
        файлов в подпись не входит.
        """
        api_key, api_secret, base_url = self._ensure_credentials()

        signed_params = {**params, "apiKey": api_key, "time": str(int(time.time()))}
        signed_params["apiSig"] = generate_signature(method_name, signed_params, api_secret)

        url = f"{base_url.rstrip('/')}/{method_name}"
        logger.info("PolygonClient.call(%s): POST %s", method_name, url)
        response = requests.post(
            url,
            data=signed_params,
            files=files,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

        try:
            body = response.json()
        except ValueError:
            raise PolygonApiError(
                method_name,
                f"ответ не является JSON (HTTP {response.status_code}): "
                f"{response.text[:200]!r}",
            ) from None

        if body.get("status") == "FAILED":
            raise PolygonApiError(method_name, body.get("comment", ""))
        return body.get("result")
