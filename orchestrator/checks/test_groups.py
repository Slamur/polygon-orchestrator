"""Test groups (subtasks) for the local checks — `local check --full`, `local run --full`.

The groups themselves and a script test's group (the `-group=<N>` marker) — in
`orchestrator/test_groups.py`, shared with the polygon steps. The validator gets the
group as testlib's `--group <N>` — as on Polygon — so a test outside its group's limits
is rejected.

Points (`group_results`): a group is passed if all its tests got OK; its points are
earned if it is passed and every group it depends on is earned too. Without
`test_groups` none of this runs: the checks behave as for a problem without groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.test_groups import EXAMPLES_GROUP, TestGroupsError
from orchestrator.test_groups import load_test_groups as load_constraints_groups

VALIDATOR_GROUP_OPTION = "--group"


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


def load_test_groups(problem_dir: Path) -> tuple[list[TestGroup] | None, list[str]]:
    """(the groups from `constraints.yaml` in their order, errors). `None` — the problem has
    no test groups (no file, no `test_groups` key or it is empty) or they are invalid (the
    error says why). A group without points has 0."""
    try:
        hints = load_constraints_groups(problem_dir)
    except TestGroupsError as exc:
        return None, [str(exc)]
    if hints is None:
        return None, []
    return [
        TestGroup(hint.name, hint.points or 0, tuple(hint.dependencies or ())) for hint in hints
    ], []


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
