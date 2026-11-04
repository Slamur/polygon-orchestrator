"""A thin HTTP client on top of the Polygon API.

Protects Polygon from a stream of requests (`pull` makes a request per file):
between two requests — no less than `_MIN_REQUEST_INTERVAL_SECONDS`; on HTTP
429 (Too Many Requests) the request is repeated with an exponential pause
(`_RETRY_DELAYS_SECONDS`, or as much as `Retry-After` asks), and the interval
between requests is doubled until the end of the client's life
(up to `_MAX_REQUEST_INTERVAL_SECONDS`) — so as not to hit the limit again.
If it's still 429 after all retries — the response goes to the caller as is
(`PolygonApiError` with HTTP 429).

The response timeout is `_REQUEST_TIMEOUT_SECONDS`; a method that Polygon
runs for a long time (`problem.saveScript` generates tests right away) passes its own
`timeout` to `call`. A timeout isn't retried: the request might have reached Polygon, and
what to do next is up to the caller.
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
    """The `comment` from the Polygon response with `status == "FAILED"`."""

    def __init__(self, method_name: str, comment: str):
        self.method_name = method_name
        self.comment = comment
        super().__init__(f"Polygon API {method_name}: {comment}")


class PolygonClient:
    """A thin HTTP client on top of the Polygon API.

    The keys are read lazily, on the first call (see `.env.example`) — the same
    approach as in `AnthropicClient._get_sdk_client`.
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
        """Returns (api_key, api_secret, base_url), reading them from the environment.

        If there's no key/secret — fail right away with a clear error rather than
        getting an obscure FAILED from Polygon. `POLYGON_API_BASE_URL`
        is optional (by default — the production Polygon).
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

    def call(
        self,
        method_name: str,
        params: dict[str, str | bytes],
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        """Calls one Polygon API method, returns `result` or raises `PolygonApiError`.

        File contents (`file` in `problem.saveFile`/`problem.saveSolution`)
        are passed in `params` on a par with the other parameters — as `bytes`
        or `str` — and are part of the signature (see `generate_signature`). The request
        goes as multipart/form-data, where each parameter is an ordinary form field
        without a file name: this way `bytes` are passed without re-encoding.
        `timeout` — how many seconds to wait for the response (see the module docstring).
        """
        response = self._post(method_name, params, timeout=timeout)

        try:
            body = response.json()
        except ValueError:
            raise PolygonApiError(
                method_name,
                f"response is not JSON (HTTP {response.status_code}): {response.text[:200]!r}",
            ) from None

        if body.get("status") == "FAILED":
            raise PolygonApiError(method_name, body.get("comment", ""))
        return body.get("result")

    def call_raw(self, method_name: str, params: dict[str, str | bytes]) -> bytes:
        """Calls a Polygon API method that on success returns not JSON but the
        contents themselves (`problem.viewFile`, `problem.viewSolution`,
        `problem.script`, `problem.testInput`) — returns the response body as
        is.

        An error of such methods comes as ordinary JSON with `status == "FAILED"`.
        It's recognized by a non-200 HTTP status; in case Polygon
        returns FAILED with HTTP 200 — also by `Content-Type: application/json`
        (one can't judge by the body alone: the downloaded file may itself be JSON).

        NOTE: it isn't verified on the real API with which HTTP status and
        Content-Type Polygon accompanies FAILED for these methods.
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
                    f"unexpected response (HTTP {response.status_code}): {response.text[:200]!r}",
                )
        return response.content

    def _post(
        self,
        method_name: str,
        params: dict[str, str | bytes],
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> requests.Response:
        """Sends a request with pauses and retries on 429 (see the module
        docstring); parsing the response is up to the caller."""
        for attempt, retry_delay in enumerate(_RETRY_DELAYS_SECONDS, start=1):
            response = self._post_once(method_name, params, timeout=timeout)
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
        return self._post_once(method_name, params, timeout=timeout)

    def _wait_for_interval(self) -> None:
        if self._last_request_at is not None:
            remaining = self._last_request_at + self._min_interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _post_once(
        self, method_name: str, params: dict[str, str | bytes], *, timeout: float
    ) -> requests.Response:
        """Signs and sends one request, keeping the interval after the
        previous one. The signature — anew for each attempt: `time` is part of it."""
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
            timeout=timeout,
        )


def _retry_after(response: requests.Response) -> float | None:
    """`Retry-After` in seconds (the date form isn't supported), no more than
    `_MAX_RETRY_AFTER_SECONDS`; `None` if there's no header or it isn't a number."""
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS) if seconds > 0 else None
