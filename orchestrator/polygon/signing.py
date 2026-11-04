"""Signing requests to the Polygon API — pure logic, no network."""

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
    """Returns the `apiSig` value for a request to the method `method_name`.

    `params` must already contain `apiKey` and `time`, but not `apiSig` — that's
    the responsibility of the calling code (`PolygonClient.call`). `rand` — 6
    alphanumeric characters; if `None`, it's generated randomly (the parameter
    is exposed for testability: a test fixes `rand` and checks a
    deterministic hash).

    Raw values are signed, without percent-encoding — that's how the Polygon server
    checks the signature. File contents (`file` in `problem.saveFile`
    etc.) are an ordinary parameter and are part of the signature too. `str` is encoded in
    UTF-8, `bytes` are taken as is, the string to hash is assembled in
    bytes. Pairs are sorted as `(name, value)`, so repeated
    names are ordered by value.
    """
    if rand is None:
        rand = "".join(secrets.choice(_RAND_ALPHABET) for _ in range(_RAND_LENGTH))

    sorted_pairs = sorted((name.encode(), _to_bytes(value)) for name, value in params.items())
    query_string = b"&".join(name + b"=" + value for name, value in sorted_pairs)
    sig_source = f"{rand}/{method_name}?".encode() + query_string + f"#{api_secret}".encode()
    return rand + hashlib.sha512(sig_source).hexdigest()


def _to_bytes(value: str | bytes) -> bytes:
    return value.encode() if isinstance(value, str) else value
