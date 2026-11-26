"""Test groups (subtasks) for the local checks — `local check --full`, `local run --full`.

The groups themselves and a script test's group (the `-group=<N>` marker) — in
`orchestrator/test_groups.py`, shared with the polygon steps. The validator gets the
group as testlib's `--group <N>` — as on Polygon — so a test outside its group's limits
is rejected.

Points (`group_results`): a group is passed if all its tests got OK; it is earned if
it is passed and every group it depends on is earned too. A `complete_group` group gives
its points if it is earned; an `each_test` group — its points divided by its number of
tests for each OK test, if every group it depends on is earned (as on Polygon, a group is
judged only after its dependencies are passed). An `each_test` group's number of tests
must divide its points (`check_test_groups`). Without
`test_groups` none of this runs: the checks behave as for a problem without groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.spec import EACH_TEST
from orchestrator.test_groups import EXAMPLES_GROUP, TestGroupsError, each_test_count_error
from orchestrator.test_groups import load_test_groups as load_constraints_groups

VALIDATOR_GROUP_OPTION = "--group"


@dataclass(frozen=True)
class TestGroup:
    __test__ = False  # not a pytest test class

    name: str
    points: int
    dependencies: tuple[str, ...] = ()
    each_test: bool = False  # points_policy each_test: the points for each passed test


@dataclass(frozen=True)
class GroupResult:
    """A solution on one group: `run` — tests run of `total`; `failed` — the first test
    without OK (its name and verdict); `earned` — the group is passed and its dependencies
    are earned; `ok` — its tests with OK; `judged` — every group it depends on is earned."""

    group: TestGroup
    total: int
    run: int
    failed: tuple[str, str] | None
    earned: bool
    ok: int = 0
    judged: bool = True

    @property
    def passed(self) -> bool:
        return self.failed is None and self.run == self.total and self.total > 0

    @property
    def points(self) -> int | float:
        if not self.group.each_test:
            return self.group.points if self.earned else 0
        if not self.judged or self.total == 0:
            return 0
        share = self.group.points * self.ok / self.total
        return int(share) if float(share).is_integer() else share

    def describe(self) -> str:
        name = self.group.name
        if self.failed is not None:
            test, verdict = self.failed
            if self.group.each_test:
                return f"{name} {self.ok}/{self.total} OK, first {verdict} on {test}" + (
                    ""
                    if self.judged
                    else f", no points (depends on {', '.join(self.group.dependencies)})"
                )
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
    return [test_group(hint) for hint in hints], []


def upstream_groups(groups: list[TestGroup]) -> dict[str, frozenset[str]]:
    """The groups each group depends on, directly or through other groups — the
    transitive closure of `dependencies`. A group's tests are judged only after all of
    these are passed."""
    direct = {group.name: group.dependencies for group in groups}
    closure: dict[str, frozenset[str]] = {}
    for name in direct:
        seen: set[str] = set()
        pending = list(direct[name])
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(direct.get(current, ()))
        closure[name] = frozenset(seen - {name})
    return closure


def test_group(hint) -> TestGroup:
    """A group of `constraints.yaml` (`TestGroupHint`) for the local checks."""
    return TestGroup(
        hint.name,
        hint.points or 0,
        tuple(hint.dependencies or ()),
        each_test=hint.points_policy == EACH_TEST,
    )


def check_test_groups(
    groups: list[TestGroup], tests: list[tuple[str, str | None, str]]
) -> list[str]:
    """Errors in how the script's tests (name, group, command) are split into groups: a test
    without the marker or of an unknown group, a group without tests, an `each_test` group
    whose number of tests doesn't divide its points."""
    known = {g.name for g in groups}
    errors = []
    for name, group, command in tests:
        if group is None:
            errors.append(
                f"{name}: no -group=<N> marker, the problem has test groups\n    {command}"
            )
        elif group not in known:
            errors.append(f"{name}: unknown test group '{group}'\n    {command}")
    counts: dict[str | None, int] = {}
    for _, group, _ in tests:
        counts[group] = counts.get(group, 0) + 1
    for group in groups:
        if group.name not in counts:
            errors.append(f"test group {group.name} has no tests in the script")
        elif group.each_test:
            error = each_test_count_error(group.name, group.points, counts[group.name])
            if error:
                errors.append(error)
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
    ok: dict[str, int] = {}
    failed: dict[str, tuple[str, str]] = {}
    for test, group, verdict in verdicts:
        if group is None:
            continue
        run[group] = run.get(group, 0) + 1
        if verdict == "OK":
            ok[group] = ok.get(group, 0) + 1
        elif group not in failed:
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
            g,
            totals.get(g.name, 0),
            run.get(g.name, 0),
            failed.get(g.name),
            is_earned(g.name),
            ok=ok.get(g.name, 0),
            judged=all(is_earned(d) for d in g.dependencies),
        )
        for g in groups
    ]
