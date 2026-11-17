"""Test groups (subtasks) for the local checks — `local check --full`, `local run --full`.

A problem has test groups if `authoring/constraints.yaml` has `test_groups`
(`templates/constraints_format.yaml`): names are numbers `1, 2, ...`, each with its
points and dependencies. Group 0 — the statement examples — isn't listed there: it has
the full limits and no points.

A script test's group is the `-group=<N>` marker on its script line (generators don't
read it, see the test-script of `examples/number/roots-subtasks`). The validator gets it
as testlib's `--group <N>` — as on Polygon — so a test outside its group's limits is
rejected.

Points (`group_results`): a group is passed if all its tests got OK; its points are
earned if it is passed and every group it depends on is earned too. Without
`test_groups` none of this runs: the checks behave as for a problem without groups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from orchestrator.problem_paths import CONSTRAINTS_FILENAME, find_authoring_file

EXAMPLES_GROUP = "0"
VALIDATOR_GROUP_OPTION = "--group"
# `-group=<N>` as a separate argument of any generator of the chain.
_GROUP_MARKER = re.compile(r"(?:^|\s)-group=(\S*)(?=\s|$)")


@dataclass(frozen=True)
class TestGroup:
    __test__ = False  # not a pytest test class

    name: str
    points: int
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupResult:
    """A solution on one group: `run` — tests run of `total`; `failed` — the first test
    without OK (its name and verdict); `earned` — the group's points are earned."""

    group: TestGroup
    total: int
    run: int
    failed: tuple[str, str] | None
    earned: bool

    @property
    def passed(self) -> bool:
        return self.failed is None and self.run == self.total and self.total > 0

    @property
    def points(self) -> int:
        return self.group.points if self.earned else 0

    def describe(self) -> str:
        name = self.group.name
        if self.failed is not None:
            test, verdict = self.failed
            return f"{name} {verdict} on {test}"
        if self.total == 0:
            return f"{name} no tests"
        if self.run < self.total:
            return f"{name} not judged ({self.total - self.run} test(s) not run)"
        if not self.earned:
            missing = ", ".join(self.group.dependencies)
            return f"{name} OK, no points (depends on {missing})"
        return f"{name} OK"


def script_test_group(command: str) -> str | None:
    """The group of a script line: the value of its last `-group=` marker, `None` — no
    marker."""
    found = _GROUP_MARKER.findall(command)
    return found[-1] if found else None


def load_test_groups(problem_dir: Path) -> tuple[list[TestGroup] | None, list[str]]:
    """(the groups from `constraints.yaml` in their order, errors). `None` — the problem has
    no test groups (no file, no `test_groups` key or it is empty)."""
    path = find_authoring_file(Path(problem_dir), CONSTRAINTS_FILENAME)
    if not path.is_file():
        return None, []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"{CONSTRAINTS_FILENAME}: not valid YAML: {exc}"]
    raw = data.get("test_groups") if isinstance(data, dict) else None
    if not raw:
        return None, []
    if not isinstance(raw, list):
        return None, [f"{CONSTRAINTS_FILENAME}: test_groups must be a list"]
    groups: list[TestGroup] = []
    errors: list[str] = []
    for index, item in enumerate(raw):
        where = f"{CONSTRAINTS_FILENAME}: test_groups[{index}]"
        if not isinstance(item, dict) or item.get("name") is None:
            errors.append(f"{where}: no name")
            continue
        name = str(item["name"]).strip()
        points = item.get("points")
        if points is None:
            points = 0
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            errors.append(f"{where}: points must be a non-negative integer, got {points!r}")
            points = 0
        dependencies = item.get("dependencies") or []
        if not isinstance(dependencies, list):
            errors.append(f"{where}: dependencies must be a list")
            dependencies = []
        groups.append(TestGroup(name, points, tuple(str(d).strip() for d in dependencies)))
    names = [g.name for g in groups]
    for group in groups:
        if not group.name.isdigit() or group.name.startswith("0"):
            errors.append(
                f"{CONSTRAINTS_FILENAME}: test group '{group.name}' — the name must be a "
                "positive number: the validator gets it as a number"
            )
        if names.count(group.name) > 1:
            errors.append(f"{CONSTRAINTS_FILENAME}: test group '{group.name}' repeats")
        for dependency in group.dependencies:
            if dependency not in names or dependency == group.name:
                errors.append(
                    f"{CONSTRAINTS_FILENAME}: test group '{group.name}' depends on "
                    f"'{dependency}', which is not another listed group"
                )
    return groups, sorted(set(errors), key=errors.index)


def check_test_groups(
    groups: list[TestGroup], tests: list[tuple[str, str | None, str]]
) -> list[str]:
    """Errors in how the script's tests (name, group, command) are split into groups: a test
    without the marker or of an unknown group, a group without tests."""
    known = {g.name for g in groups}
    errors = []
    for name, group, command in tests:
        if group is None:
            errors.append(
                f"{name}: no -group=<N> marker, the problem has test groups\n    {command}"
            )
        elif group not in known:
            errors.append(f"{name}: unknown test group '{group}'\n    {command}")
    present = {group for _, group, _ in tests}
    for group in groups:
        if group.name not in present:
            errors.append(f"test group {group.name} has no tests in the script")
    return errors


def count_by_group(tests: list[tuple[str, str | None, str]], examples: int) -> str:
    """`test groups: 0 (examples): 3, 1: 10, ...` — how many tests each group has."""
    counts: dict[str, int] = {}
    for _, group, _ in tests:
        key = group if group is not None else "?"
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{EXAMPLES_GROUP} (examples): {examples}"] if examples else []
    order = sorted(
        counts, key=lambda name: (not name.isdigit(), int(name) if name.isdigit() else 0)
    )
    parts += [f"{name}: {counts[name]}" for name in order]
    return "test groups: " + ", ".join(parts)


def group_results(
    groups: list[TestGroup],
    totals: dict[str, int],
    verdicts: list[tuple[str, str | None, str]],
) -> list[GroupResult]:
    """A solution's results by groups. `totals` — how many script tests each group has;
    `verdicts` — (test name, group, verdict) of the script tests the solution was run on,
    in order."""
    by_name = {g.name: g for g in groups}
    run: dict[str, int] = {}
    failed: dict[str, tuple[str, str]] = {}
    for test, group, verdict in verdicts:
        if group is None:
            continue
        run[group] = run.get(group, 0) + 1
        if verdict != "OK" and group not in failed:
            failed[group] = (test, verdict)

    def passed(name: str) -> bool:
        return name not in failed and run.get(name, 0) == totals.get(name, 0) > 0

    earned: dict[str, bool] = {}

    def is_earned(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name not in earned:
            group = by_name.get(name)
            if group is None or name in seen:
                return False
            earned[name] = passed(name) and all(
                is_earned(d, seen | {name}) for d in group.dependencies
            )
        return earned[name]

    return [
        GroupResult(
            g, totals.get(g.name, 0), run.get(g.name, 0), failed.get(g.name), is_earned(g.name)
        )
        for g in groups
    ]
