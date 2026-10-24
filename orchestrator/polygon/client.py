"""Тонкий HTTP-клиент поверх Polygon API.

Бережёт Polygon от потока запросов (`pull` делает по запросу на каждый файл):
между двумя запросами — не меньше `_MIN_REQUEST_INTERVAL_SECONDS`; на HTTP
429 (Too Many Requests) запрос повторяется с экспоненциальной паузой
(`_RETRY_DELAYS_SECONDS`, или сколько просит `Retry-After`), а интервал
между запросами удваивается до конца жизни клиента
(до `_MAX_REQUEST_INTERVAL_SECONDS`) — чтобы дальше в лимит не упираться.
Если и после всех повторов 429 — ответ уходит вызывающему как есть
(`PolygonApiError` с HTTP 429).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

import requests
from dotenv import load_dotenv

from orchestrator.polygon.signing import generate_signature

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://polygon.codeforces.com/api"
_REQUEST_TIMEOUT_SECONDS = 60
_MIN_REQUEST_INTERVAL_SECONDS = 0.5
_MAX_REQUEST_INTERVAL_SECONDS = 5.0
_RETRY_DELAYS_SECONDS = (5, 10, 20, 40, 80)
_MAX_RETRY_AFTER_SECONDS = 300
_TOO_MANY_REQUESTS = 429


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

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key: str | None = None
        self._api_secret: str | None = None
        self._base_url: str | None = None
        self._env_loaded = False
        self._sleep = sleep
        self._clock = clock
        self._min_interval = _MIN_REQUEST_INTERVAL_SECONDS
        self._last_request_at: float | None = None

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
        """Отправляет запрос с паузами и повторами на 429 (см. докстринг
        модуля); разбор ответа — у вызывающего."""
        for attempt, retry_delay in enumerate(_RETRY_DELAYS_SECONDS, start=1):
            response = self._post_once(method_name, params)
            if response.status_code != _TOO_MANY_REQUESTS:
                return response
            self._min_interval = min(self._min_interval * 2, _MAX_REQUEST_INTERVAL_SECONDS)
            delay = _retry_after(response) or retry_delay
            logger.warning(
                "PolygonClient.call(%s): HTTP 429, retry %d/%d in %ss "
                "(interval between requests is now %ss)",
                method_name,
                attempt,
                len(_RETRY_DELAYS_SECONDS),
                delay,
                self._min_interval,
            )
            self._sleep(delay)
        return self._post_once(method_name, params)

    def _wait_for_interval(self) -> None:
        if self._last_request_at is not None:
            remaining = self._last_request_at + self._min_interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _post_once(self, method_name: str, params: dict[str, str | bytes]) -> requests.Response:
        """Подписывает и отправляет один запрос, выдержав интервал после
        предыдущего. Подпись — заново на каждую попытку: в неё входит `time`."""
        self._wait_for_interval()
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


def _retry_after(response: requests.Response) -> float | None:
    """`Retry-After` в секундах (форма с датой не поддерживается), не больше
    `_MAX_RETRY_AFTER_SECONDS`; `None`, если заголовка нет или он не число."""
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS) if seconds > 0 else None
