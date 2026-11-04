"""The problem's actual TL/ML — `problems/<problem path>/limits.yaml`.

`constraints.yaml` is the result of `constraints_pick`: the TL/ML in it are what
the model proposed (or the author set in the spec), and it doesn't change after generation.
The actual TL/ML — those set on Polygon — are stored in
`limits.yaml`: `polygon push` uploads it (`set_constraints`), `polygon pull` compares the
problem's TL/ML with it, and it can be edited by hand. It is derived from
`constraints.yaml` by the `limits_from_constraints` step
(`orchestrator/steps/limits_from_constraints.py`) — only when the TL/ML in
`constraints.yaml` changed, so an edit of the TL/ML on Polygon or by hand
survives `llm generate`.

The keys are the same as in `constraints.yaml` (`limits.time_limit_seconds`,
`limits.memory_limit_mb`), at the document root.
"""

from __future__ import annotations

from numbers import Real
from typing import Any

import yaml

LIMITS_FILENAME = "limits.yaml"

TIME_KEY = "time_limit_seconds"
MEMORY_KEY = "memory_limit_mb"

LIMITS_HEADER = (
    "# Current TL/ML of the problem: polygon push sets them on Polygon, polygon pull\n"
    "# updates them from Polygon. Derived from constraints.yaml by the step\n"
    "# limits_from_constraints only when the TL/ML there change — edit freely.\n"
)


class LimitsError(ValueError):
    """The TL/ML can't be read: not YAML, no keys, not positive numbers."""


def extract_limits(data: dict[str, Any]) -> tuple[object, object]:
    """The pair (time_limit_seconds, memory_limit_mb) from parsed YAML.

    `constraints.yaml` is free-form YAML: the model's structured output
    validates only the status/artifacts/notes shape, not the file's contents.
    So the pair occurs both inside a nested `limits` dict and right
    at the document root (as in `limits.yaml`) — we look in both places.

    If the pair is in both places with DIFFERENT values — that's a contradiction
    in the file itself: a `LimitsError`, not a silent choice. The values aren't
    checked (they may be `None`) — `parse_limits` does that.
    """
    candidates: list[tuple[str, dict]] = []
    limits = data.get("limits")
    if isinstance(limits, dict):
        candidates.append(("limits", limits))
    candidates.append(("root", data))

    found = [
        (label, (container[TIME_KEY], container[MEMORY_KEY]))
        for label, container in candidates
        if TIME_KEY in container and MEMORY_KEY in container
    ]
    if not found:
        raise LimitsError(f"{TIME_KEY}/{MEMORY_KEY} found neither in 'limits' nor at the root")

    first_values = found[0][1]
    if any(values != first_values for _, values in found[1:]):
        raise LimitsError(
            f"{TIME_KEY}/{MEMORY_KEY} are set both in 'limits' and at the root, "
            f"with different values: {dict(found)!r}"
        )
    return first_values


def parse_limits(text: str) -> tuple[int, int]:
    """(timeLimit in milliseconds, memoryLimit in megabytes) — in the units of
    `problem.updateInfo` — from the text of `limits.yaml` or `constraints.yaml`.

    Units: `time_limit_seconds` x 1000 -> milliseconds; `memory_limit_mb`
    — as is, in megabytes (the Codeforces/Polygon problem.xml convention).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LimitsError(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise LimitsError(f"did not parse into a mapping (got {type(data).__name__})")

    time_limit_seconds, memory_limit_mb = extract_limits(data)
    for key, value in ((TIME_KEY, time_limit_seconds), (MEMORY_KEY, memory_limit_mb)):
        if value is None:
            raise LimitsError(f"{key} is not set (null)")
        if isinstance(value, bool) or not isinstance(value, Real) or value <= 0:
            raise LimitsError(f"{key} must be a positive number, got {value!r}")
    return round(time_limit_seconds * 1000), round(memory_limit_mb)


def render_limits(time_limit_ms: int, memory_limit_mb: int) -> str:
    """The text of `limits.yaml` for TL/ML in the units of `parse_limits`."""
    seconds = time_limit_ms / 1000
    return (
        f"{LIMITS_HEADER}"
        f"{TIME_KEY}: {int(seconds) if seconds.is_integer() else seconds}\n"
        f"{MEMORY_KEY}: {memory_limit_mb}\n"
    )
