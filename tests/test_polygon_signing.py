import hashlib
import re

from orchestrator.polygon.signing import generate_signature


def _expected(rand: str, sig_source: str) -> str:
    """The expected signature from a manually assembled sig_source string."""
    return rand + hashlib.sha512(sig_source.encode()).hexdigest()


def test_signature_matches_hand_built_sig_source():
    params = {"problemId": "42", "time": "1700000000", "apiKey": "key123"}

    signature = generate_signature("problem.info", params, "sec", rand="abcdef")

    # The names are sorted: apiKey < problemId < time.
    assert signature == _expected(
        "abcdef",
        "abcdef/problem.info?apiKey=key123&problemId=42&time=1700000000#sec",
    )


def test_signature_is_deterministic_for_fixed_rand():
    params = {"apiKey": "k", "time": "1", "name": "x"}

    first = generate_signature("problem.create", params, "s", rand="qwerty")
    second = generate_signature("problem.create", dict(params), "s", rand="qwerty")

    assert first == second


def test_signature_does_not_depend_on_params_insertion_order():
    forward = {"a": "1", "b": "2", "c": "3"}
    backward = {"c": "3", "b": "2", "a": "1"}

    assert generate_signature("m", forward, "s", rand="000000") == generate_signature(
        "m", backward, "s", rand="000000"
    )


def test_signature_sorts_names_bytewise_uppercase_before_lowercase():
    # The sorting is the ordinary Python sorting by (name, value): "Z" (0x5A)
    # before "a" (0x61), not case-insensitive.
    signature = generate_signature("m", {"a": "1", "Z": "2"}, "s", rand="000000")

    assert signature == _expected("000000", "000000/m?Z=2&a=1#s")


def test_signature_uses_raw_non_ascii_values():
    params = {"time": "1", "name": "Задача A", "apiKey": "k"}

    signature = generate_signature("problem.create", params, "s", rand="zzzzzz")

    # Without percent-encoding: the value in UTF-8 as is, a space is a space.
    assert signature == _expected(
        "zzzzzz",
        "zzzzzz/problem.create?apiKey=k&name=Задача A&time=1#s",
    )


def test_signature_uses_raw_reserved_characters():
    # "&", "=", "/", "#" and line breaks inside a value are signed as
    # is — the same way the Polygon server signs them.
    signature = generate_signature("m", {"q": "a&b=c/d#e\nf"}, "s", rand="000000")

    assert signature == _expected("000000", "000000/m?q=a&b=c/d#e\nf#s")


def test_signature_includes_bytes_values_as_is():
    content = b"#include <cstdio>\nint main() {}\n"

    signature = generate_signature("m", {"file": content, "name": "a.cpp"}, "s", rand="000000")

    expected_source = b"000000/m?file=" + content + b"&name=a.cpp#s"
    assert signature == "000000" + hashlib.sha512(expected_source).hexdigest()


def test_signature_depends_on_secret():
    params = {"apiKey": "k", "time": "1"}

    assert generate_signature("m", params, "secret-1", rand="000000") != generate_signature(
        "m", params, "secret-2", rand="000000"
    )


def test_signature_generates_random_prefix_when_rand_is_none():
    signature = generate_signature("m", {"apiKey": "k", "time": "1"}, "s")

    # 6 alphanumeric characters of rand + 128 hex characters of sha512.
    assert re.fullmatch(r"[a-z0-9]{6}[0-9a-f]{128}", signature)
