"""Test groups (subtasks) of a problem — shared by the local checks
(`orchestrator/checks/test_groups.py`) and the polygon steps
(`orchestrator/polygon/steps/upload_test_groups.py`, `polygon pull`).

A problem has test groups if `authoring/constraints.yaml` has a non-empty `test_groups`
(`templates/constraints_format.yaml`): names are numbers `1, 2, ...` — the validator gets
the group as a number — each with its points, dependencies and feedback policy. Group `0`
— the statement examples — isn't listed there: the full limits, no points.

A script test's group is the `-group=<N>` marker on its script line (generators don't
read it, see the test-script of `examples/number/roots-subtasks`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from orchestrator.problem_paths import CONSTRAINTS_FILENAME, find_authoring_file
from orchestrator.spec import TestGroupHint, allowed_test_counts, check_test_groups

EXAMPLES_GROUP = "0"
TEST_GROUPS_KEY = "test_groups"

# `-group=<N>` as a separate argument of any generator of the chain.
_GROUP_MARKER = re.compile(r"(?:^|\s)-group=(\S*)(?=\s|$)")
_HINT_FIELDS = frozenset(TestGroupHint.model_fields)


class TestGroupsError(ValueError):
    """`constraints.yaml` has `test_groups`, but they can't be read or are invalid."""

    __test__ = False  # not a pytest test class


def group_marker(script_line: str) -> str | None:
    """The group of a script line: the value of its last `-group=` marker before the
    output redirection `>`; `None` — no marker."""
    found = _GROUP_MARKER.findall(script_line.split(">", 1)[0])
    return found[-1] if found else None


def each_test_count_error(name: str, points: int, count: int) -> str | None:
    """Why an `each_test` group with `points` can't have `count` tests — every test is
    worth the same whole number of points, so the count must divide the points; `None` —
    it can."""
    if count <= 0 or points % count == 0:
        return None
    allowed = ", ".join(str(value) for value in allowed_test_counts(points))
    return (
        f"test group {name} ({points} points, points_policy each_test) has {count} test(s) — "
        f"each test is worth the same whole number of points, so the number of tests must "
        f"divide {points}: {allowed}"
    )


def constraints_path(problem_dir: Path) -> Path:
    return find_authoring_file(Path(problem_dir), CONSTRAINTS_FILENAME)


def load_test_groups(problem_dir: Path) -> list[TestGroupHint] | None:
    """The groups of `constraints.yaml` in their order; `None` — the problem has no test
    groups (no file, no `test_groups` key or it is empty). Unreadable or invalid groups —
    `TestGroupsError`. Fields of a group that aren't the spec's `test_groups_hint` fields
    (`status`, ...) are ignored."""
    path = constraints_path(problem_dir)
    if not path.is_file():
        return None
    return parse_test_groups(path.read_text(encoding="utf-8"))


def parse_test_groups(text: str) -> list[TestGroupHint] | None:
    """`load_test_groups` over a `constraints.yaml` text (e.g. a model's response before
    it is written): `None` — no groups, invalid ones — `TestGroupsError`."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TestGroupsError(f"{CONSTRAINTS_FILENAME} is not valid YAML: {exc}") from exc
    raw = data.get(TEST_GROUPS_KEY) if isinstance(data, dict) else None
    if not raw:
        return None
    where = f"{CONSTRAINTS_FILENAME}: {TEST_GROUPS_KEY}"
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise TestGroupsError(f"{where} must be a list of groups")
    try:
        groups = [
            TestGroupHint(**{key: value for key, value in item.items() if key in _HINT_FIELDS})
            for item in with_string_names(raw)
        ]
        check_test_groups(groups, where)
    except ValidationError as exc:
        raise TestGroupsError(f"{where}: {exc}") from exc
    except ValueError as exc:
        raise TestGroupsError(str(exc)) from exc
    return groups


def raw_test_groups(text: str) -> list[dict]:
    """The `test_groups` items of a `constraints.yaml` text as they are (names as strings),
    with all their fields; no groups — `[]`."""
    data = yaml.safe_load(text)
    raw = data.get(TEST_GROUPS_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return with_string_names(item for item in raw if isinstance(item, dict))


def replace_test_groups(text: str, groups: list[dict]) -> str:
    """The `constraints.yaml` text with its top-level `test_groups` block replaced by
    `groups`; the rest of the file — comments included — stays as is. Comments inside
    the block are lost. No such block — `ValueError`."""
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"{TEST_GROUPS_KEY}\s*:", line)), None
    )
    if start is None:
        raise ValueError(f"no top-level {TEST_GROUPS_KEY} in {CONSTRAINTS_FILENAME}")
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1  # the blank lines before the next key stay
    block = [f"{TEST_GROUPS_KEY}:\n"]
    for group in groups:
        for position, (key, value) in enumerate(group.items()):
            prefix = "  - " if position == 0 else "    "
            block.append(f"{prefix}{key}: {_yaml_scalar(value)}\n")
    return "".join(lines[:start] + block + lines[end:])


_PLAIN_WORD = re.compile(r"[a-z][a-z_]*")


def _yaml_scalar(value: object) -> str:
    """A value of a group's field on one line, in the style of `constraints_format.yaml`:
    names and other strings in double quotes, words like `icpc` — plain, lists — in flow
    style."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str):
        plain = _PLAIN_WORD.fullmatch(value) and value not in ("null", "true", "false")
        return value if plain else json.dumps(value, ensure_ascii=False)
    if isinstance(value, list) and all(isinstance(item, str | int) for item in value):
        return json.dumps(value, ensure_ascii=False)
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()


def with_string_names(raw: Iterable[dict]) -> list[dict]:
    """YAML reads `name: 1` as a number, `dependencies: [1]` as numbers too."""
    items = []
    for item in raw:
        item = dict(item)
        if item.get("name") is not None:
            item["name"] = str(item["name"]).strip()
        if isinstance(item.get("dependencies"), list):
            item["dependencies"] = [str(name).strip() for name in item["dependencies"]]
        items.append(item)
    return items
