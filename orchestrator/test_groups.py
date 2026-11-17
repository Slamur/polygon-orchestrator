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

import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from orchestrator.problem_paths import CONSTRAINTS_FILENAME, find_authoring_file
from orchestrator.spec import TestGroupHint, check_test_groups

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
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def with_string_names(raw: Iterable[dict]) -> list[dict]:
    """YAML reads `name: 1` as a number, `dependencies: [1]` as numbers too."""
    items = []
    for item in raw:
        item = dict(item)
        if item.get("name") is not None:
            item["name"] = str(item["name"]).strip()
        if isinstance(item.get("dependencies"), list):
            item["dependencies"] = [str(name).strip() for name in item["dependencies"]]
        if isinstance(item.get("subset_of"), int):
            item["subset_of"] = str(item["subset_of"])
        items.append(item)
    return items
