"""Подпись запросов к Polygon API — чистая логика, без сети."""

from __future__ import annotations

import hashlib
import secrets
import string

_RAND_ALPHABET = string.ascii_lowercase + string.digits
_RAND_LENGTH = 6


def generate_signature(
    method_name: str,
    params: dict[str, str | bytes],
    api_secret: str,
    rand: str | None = None,
) -> str:
    """Возвращает значение `apiSig` для запроса к методу `method_name`.

    `params` должен уже содержать `apiKey` и `time`, но не `apiSig` — это
    ответственность вызывающего кода (`PolygonClient.call`). `rand` — 6
    alphanumeric символов; если `None`, генерируется случайно (параметр
    вынесен ради тестируемости: тест фиксирует `rand` и проверяет
    детерминированный хеш).

    Подписываются сырые значения, без percent-encoding — так подпись
    проверяет сервер Polygon. Содержимое файла (`file` в `problem.saveFile`
    и т.п.) — обычный параметр и тоже входит в подпись. `str` кодируется в
    UTF-8, `bytes` берутся как есть, строка для хеширования собирается в
    байтах. Пары сортируются как `(name, value)`, так что повторяющиеся
    имена упорядочиваются по значению.
    """
    if rand is None:
        rand = "".join(secrets.choice(_RAND_ALPHABET) for _ in range(_RAND_LENGTH))

    sorted_pairs = sorted((name.encode(), _to_bytes(value)) for name, value in params.items())
    query_string = b"&".join(name + b"=" + value for name, value in sorted_pairs)
    sig_source = f"{rand}/{method_name}?".encode() + query_string + f"#{api_secret}".encode()
    return rand + hashlib.sha512(sig_source).hexdigest()


def _to_bytes(value: str | bytes) -> bytes:
    return value.encode() if isinstance(value, str) else value
