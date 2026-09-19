"""Подпись запросов к Polygon API — чистая логика, без сети."""

from __future__ import annotations

import hashlib
import secrets
import string
from urllib.parse import quote

_RAND_ALPHABET = string.ascii_lowercase + string.digits
_RAND_LENGTH = 6


def generate_signature(
    method_name: str,
    params: dict[str, str],
    api_secret: str,
    rand: str | None = None,
) -> str:
    """Возвращает значение `apiSig` для запроса к методу `method_name`.

    `params` должен уже содержать `apiKey` и `time`, но не `apiSig` — это
    ответственность вызывающего кода (`PolygonClient.call`). `rand` — 6
    alphanumeric символов; если `None`, генерируется случайно (параметр
    вынесен ради тестируемости: тест фиксирует `rand` и проверяет
    детерминированный хеш).

    Значения percent-encoded (`quote(..., safe="")`) до сортировки и
    подписи — подписывается то представление, которое реально уйдёт в
    запрос, а не сырое. Пары сортируются как `(name, value)`, так что
    повторяющиеся имена упорядочиваются по значению.
    """
    if rand is None:
        rand = "".join(secrets.choice(_RAND_ALPHABET) for _ in range(_RAND_LENGTH))

    sorted_pairs = sorted((name, quote(value, safe="")) for name, value in params.items())
    query_string = "&".join(f"{name}={value}" for name, value in sorted_pairs)
    sig_source = f"{rand}/{method_name}?{query_string}#{api_secret}"
    return rand + hashlib.sha512(sig_source.encode()).hexdigest()
