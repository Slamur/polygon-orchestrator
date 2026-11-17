"""A Polygon step — test groups (subtasks) and their points, by the `test_groups` of
`authoring/constraints.yaml` (the result of `constraints_pick`). No `test_groups`
there (or no file) — the step does nothing: a problem without groups is synced
exactly as before.

Polygon doesn't take groups from the test-script: a test's group is set
separately. So the step goes after `upload_test_script` — the tests are already
generated — and takes each test's group from Polygon's own test list
(`problem.tests`):

- a generated test — by the `-group=<name>` marker of its `scriptLine` (the
  script adds it to every line, see `templates/tests_plan_format.yaml`); a
  generated test without the marker or with a group not in `constraints.yaml`
  is a `PolygonStepError` before anything is changed;
- a manual test (the statement examples, `upload_statement`) — group `0`.

Then, by docs/POLYGON_API.md:

1. `problem.enableGroups` (`testset=tests`) and `problem.enablePoints`;
2. `problem.setTestGroup` with `testIndices` — one call per group, only for the
   tests whose group on Polygon differs;
3. `problem.saveTest` with `testPoints` — the group's points on its last test (the
   largest index), 0 on the others; only for the tests whose points differ;
4. `problem.saveTestGroup` for each group: `pointsPolicy=COMPLETE_GROUP` (the
   points only for passing every test of the group), `feedbackPolicy` from
   `feedback_policy` (`icpc` -> `ICPC`, the first error), `dependencies` — the
   group names joined with a comma. A group exists on Polygon once a test is
   in it (`saveTestGroup` only edits one), so a group without tests is an error
   before any change.

Group `0` (the examples) isn't listed in `constraints.yaml`: 0 points, no
dependencies, feedback `COMPLETE` — the examples are in the statement anyway.

What counts as changed — two keys of the sync base:

- `TEST_GROUPS_KEY` — the groups' settings (`groups_digest`): computed locally,
  `polygon status` compares it;
- `TEST_GROUPS_TESTS_KEY` — the layout of the tests on Polygon the groups were
  assigned to (`tests_digest`: indices, manual or the script line). Polygon
  regenerates the tests when the script changes, and the assignment has to
  be redone — this is visible only from Polygon, so the step always makes one
  `problem.tests` request; `polygon status` doesn't compare this key.

Groups removed from `constraints.yaml` aren't turned off on Polygon — the author does
that (just as other steps don't delete files on Polygon).

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    STATUS_DONE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import (
    TEST_GROUPS_KEY,
    TEST_GROUPS_TESTS_KEY,
    digest,
)
from orchestrator.problem_paths import CONSTRAINTS_FILENAME, authoring_path
from orchestrator.test_groups import (
    EXAMPLES_GROUP,
    TestGroupsError,
    constraints_path,
    group_marker,
    load_test_groups,
)

STEP_NAME = "upload_test_groups"

NO_GROUPS_MESSAGE = f"no test_groups in {CONSTRAINTS_FILENAME}, skipped"

_TESTSET = "tests"

POINTS_POLICY = "COMPLETE_GROUP"
EXAMPLES_FEEDBACK_POLICY = "COMPLETE"
# `feedback_policy` of constraints.yaml -> `feedbackPolicy` of `problem.saveTestGroup`.
FEEDBACK_POLICIES = {"icpc": "ICPC", "points": "POINTS", "complete": "COMPLETE", "none": "NONE"}
FEEDBACK_POLICY_NAMES = {value: key for key, value in FEEDBACK_POLICIES.items()}


@dataclass(frozen=True)
class GroupSettings:
    """A test group as Polygon stores it: the points are those of the whole group
    (on Polygon — the sum of its tests' points)."""

    name: str
    points: float
    dependencies: tuple[str, ...]
    feedback_policy: str  # Polygon's value: ICPC, POINTS, COMPLETE, NONE
    points_policy: str = POINTS_POLICY


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    groups = read_groups(ctx.problems_dir / ctx.problem_id)
    if groups is None:
        return StepResult(NO_GROUPS_MESSAGE)
    polygon_id = require_polygon_state(ctx).polygon_id
    params = {"problemId": str(polygon_id)}

    tests = client.call("problem.tests", {**params, "testset": _TESTSET, "noInputs": "true"}) or []
    items = changed_items(
        ctx, {TEST_GROUPS_KEY: groups_digest(groups), TEST_GROUPS_TESTS_KEY: tests_digest(tests)}
    )
    if not items:
        return StepResult(NO_CHANGES_MESSAGE)

    by_name = {group.name: group for group in groups}
    assignment = assign_groups(tests, by_name)
    _check_every_group_has_tests(assignment, by_name)
    points = _test_points(assignment, by_name)

    client.call("problem.enableGroups", {**params, "testset": _TESTSET, "enable": "true"})
    client.call("problem.enablePoints", {**params, "enable": "true"})

    current = {test["index"]: test for test in tests}
    moved = 0
    for name in by_name:
        indices = [
            index
            for index, group in sorted(assignment.items())
            if group == name and str(current[index].get("group", "")) != name
        ]
        if indices:
            client.call(
                "problem.setTestGroup",
                {
                    **params,
                    "testset": _TESTSET,
                    "testGroup": name,
                    "testIndices": ",".join(str(index) for index in indices),
                },
            )
            moved += len(indices)

    repointed = 0
    for index, value in sorted(points.items()):
        if _points(current[index].get("points")) != value:
            client.call(
                "problem.saveTest",
                {
                    **params,
                    "testset": _TESTSET,
                    "testIndex": str(index),
                    "testPoints": _format_points(value),
                },
            )
            repointed += 1

    for group in groups:
        client.call(
            "problem.saveTestGroup",
            {
                **params,
                "testset": _TESTSET,
                "group": group.name,
                "pointsPolicy": group.points_policy,
                "feedbackPolicy": group.feedback_policy,
                "dependencies": ",".join(group.dependencies),
            },
        )

    return StepResult(
        f"set {len(groups)} test group(s) on Polygon id={polygon_id}: "
        f"{moved} test(s) moved between groups, points changed on {repointed} test(s)",
        items,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base, the groups' settings only: the tests on Polygon aren't
    visible without the network. No groups locally or in the base — nothing to do."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    problem_dir = Path(problems_dir) / problem_id
    path = authoring_path(problem_dir, CONSTRAINTS_FILENAME)
    if TEST_GROUPS_KEY not in synced and not _has_groups(path):
        return STATUS_DONE, "no test groups"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=TEST_GROUPS_KEY,
        read_items=lambda: {TEST_GROUPS_KEY: groups_digest(read_groups(problem_dir) or [])},
    )


def _has_groups(path: Path) -> bool:
    """Whether `constraints.yaml` has a non-empty `test_groups` (without validating them)."""
    if not path.is_file():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return True  # unreadable — `sync_status` reports it
    return isinstance(data, dict) and bool(data.get("test_groups"))


def read_groups(problem_dir: Path) -> list[GroupSettings] | None:
    """The groups of `authoring/constraints.yaml`, group `0` (the examples) first;
    `None` — no file or no `test_groups` in it. Invalid groups or groups without
    points — `PolygonStepError`."""
    try:
        hints = load_test_groups(problem_dir)
    except TestGroupsError as exc:
        raise PolygonStepError(f"{constraints_path(problem_dir)}: {exc}") from exc
    if hints is None:
        return None
    missing = [hint.name for hint in hints if hint.points is None]
    if missing:
        raise PolygonStepError(
            f"{constraints_path(problem_dir)}: test_groups without points: {missing}"
        )
    return [
        GroupSettings(EXAMPLES_GROUP, 0, (), EXAMPLES_FEEDBACK_POLICY),
        *(
            GroupSettings(
                hint.name,
                float(hint.points),
                tuple(hint.dependencies or ()),
                FEEDBACK_POLICIES[hint.feedback_policy],
            )
            for hint in hints
        ),
    ]


def groups_digest(groups: Iterable[GroupSettings]) -> str:
    """The sync base value of the groups' settings — the same for the local
    `constraints.yaml` (push) and for what `problem.viewTestGroup` and the tests'
    points show (`polygon pull`)."""
    canonical = [
        {
            "name": group.name,
            "points": _format_points(group.points),
            "dependencies": sorted(group.dependencies),
            "feedbackPolicy": group.feedback_policy,
            "pointsPolicy": group.points_policy,
        }
        for group in sorted(groups, key=lambda group: _name_order(group.name))
    ]
    return digest(json.dumps(canonical, sort_keys=True))


def remote_groups(test_groups: list[dict], tests: list[dict]) -> list[GroupSettings]:
    """The groups on Polygon (`problem.viewTestGroup` — TestGroup objects) with the
    points of each — the sum of its tests' points (`problem.tests`)."""
    points: dict[str, float] = {}
    for test in tests:
        group = str(test.get("group", ""))
        points[group] = points.get(group, 0.0) + _points(test.get("points"))
    return [
        GroupSettings(
            name=str(group["name"]),
            points=points.get(str(group["name"]), 0.0),
            dependencies=tuple(str(dep) for dep in group.get("dependencies") or ()),
            feedback_policy=str(group.get("feedbackPolicy", "")),
            points_policy=str(group.get("pointsPolicy", "")),
        )
        for group in test_groups
    ]


def assigned_by_script(tests: list[dict], groups: list[GroupSettings]) -> bool:
    """Whether every test on Polygon is in the group the step would put it in."""
    try:
        assignment = assign_groups(tests, {group.name: group for group in groups})
    except PolygonStepError:
        return False
    return all(str(test.get("group", "")) == assignment[test["index"]] for test in tests)


def tests_digest(tests: list[dict]) -> str:
    """The sync base value of the test layout on Polygon (`problem.tests`): what
    the groups were assigned to."""
    layout = [
        f"{test['index']}:" + ("manual" if test.get("manual") else _command(test))
        for test in sorted(tests, key=lambda test: test["index"])
    ]
    return digest("\n".join(layout))


def assign_groups(tests: list[dict], groups: dict[str, GroupSettings]) -> dict[int, str]:
    """Test index -> its group (see the module docstring); a test that can't be
    assigned — `PolygonStepError` listing such tests."""
    assignment: dict[int, str] = {}
    problems: list[str] = []
    for test in tests:
        index = test["index"]
        if test.get("manual"):
            assignment[index] = EXAMPLES_GROUP
            continue
        group = group_marker(test.get("scriptLine", ""))
        if group is None:
            problems.append(f"test {index} has no -group=<name> in its script line")
        elif group not in groups or group == EXAMPLES_GROUP:
            problems.append(f"test {index}: group {group!r} is not in test_groups")
        else:
            assignment[index] = group
    if problems:
        raise PolygonStepError(
            f"cannot assign test groups on Polygon ({CONSTRAINTS_FILENAME}): "
            + "; ".join(problems[:10])
            + (f"; and {len(problems) - 10} more" if len(problems) > 10 else "")
        )
    return assignment


def _check_every_group_has_tests(assignment: dict[int, str], groups: dict[str, GroupSettings]):
    empty = sorted(set(groups) - set(assignment.values()), key=_name_order)
    if empty:
        raise PolygonStepError(
            f"test group(s) {empty} of {CONSTRAINTS_FILENAME} have no tests on Polygon — "
            "Polygon creates a group only with its first test; check the test script"
        )


def _test_points(assignment: dict[int, str], groups: dict[str, GroupSettings]) -> dict[int, float]:
    """Test index -> its points: the group's points on its last test, 0 elsewhere."""
    points = dict.fromkeys(assignment, 0.0)
    for name, group in groups.items():
        last = max(index for index, assigned in assignment.items() if assigned == name)
        points[last] = group.points
    return points


def _points(value: object) -> float:
    return float(value) if value not in (None, "") else 0.0


def _format_points(value: float) -> str:
    return f"{value:g}"


def _command(test: dict) -> str:
    return " ".join(str(test.get("scriptLine", "")).split(">", 1)[0].split())


def _name_order(name: str) -> tuple[int, str]:
    return (int(name), name) if name.isdigit() else (1 << 30, name)


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
