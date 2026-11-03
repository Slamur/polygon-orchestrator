import pytest

from orchestrator.limits import LimitsError, parse_limits, render_limits


@pytest.mark.parametrize(
    "text, expected",
    [
        ("time_limit_seconds: 2\nmemory_limit_mb: 256\n", (2000, 256)),
        # 2.01 * 1000 == 2009.9999999999998 — int() дал бы 2009
        ("limits:\n  time_limit_seconds: 2.01\n  memory_limit_mb: 64\n", (2010, 64)),
        (
            "time_limit_seconds: 2\nmemory_limit_mb: 256\n"
            "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
            (2000, 256),
        ),
        (render_limits(1500, 512), (1500, 512)),
    ],
    ids=["root", "nested-fractional", "same-in-both-places", "rendered"],
)
def test_parse_limits(text, expected):
    assert parse_limits(text) == expected


@pytest.mark.parametrize(
    "text, message",
    [
        ("- a\n", "mapping"),
        ("groups: []\n", "found neither"),
        ("time_limit_seconds: null\nmemory_limit_mb: 256\n", "not set"),
        ("time_limit_seconds: 0\nmemory_limit_mb: 256\n", "positive number"),
        ("time_limit_seconds: -1\nmemory_limit_mb: 256\n", "positive number"),
        ("time_limit_seconds: '2'\nmemory_limit_mb: 256\n", "positive number"),
        ("time_limit_seconds: true\nmemory_limit_mb: 256\n", "positive number"),
        (
            "time_limit_seconds: 1\nmemory_limit_mb: 256\n"
            "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
            "different values",
        ),
    ],
)
def test_parse_limits_errors(text, message):
    with pytest.raises(LimitsError, match=message):
        parse_limits(text)
