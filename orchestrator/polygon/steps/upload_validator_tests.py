"""A Polygon step — the validator tests (`problem.saveValidatorTest`), after
`upload_test_groups`.

The tests are `validator_tests.yaml` (the `validator_tests` step), in their order: test K on
Polygon is the K-th of the file. No file — nothing to upload. A test with a group is saved
with `testGroup`: Polygon takes a group only once the groups are enabled for the testset,
which `upload_test_groups` does — so this step goes after it. A test without a group is
saved without `testGroup`. `testset` isn't sent: a problem has one testset, `tests`.

What counts as changed — a sync base key per test, `validator_tests/<K>`
(`validator_test_digest`: the input, line endings and trailing spaces dropped — so a test
Polygon stores with `\\r\\n` is the same —, the verdict and the group). A test that differs
from the base is saved over the K-th test on Polygon (`testIndex=K`). When something changed,
the step reads `problem.validatorTests` first and saves only the tests that differ from what
Polygon has. Polygon tests after the last local one stay: the API has no method to delete a
validator test — the step says which ones (the `notice`), the author deletes them by hand.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.checks.test_groups import load_test_groups
from orchestrator.checks.validator_tests import (
    VALIDATOR_TESTS_FILENAME,
    ValidatorTest,
    ValidatorTestsError,
    load_validator_tests,
)
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
from orchestrator.polygon.sync import VALIDATOR_TESTS_KEY_PREFIX, digest, validator_test_key

STEP_NAME = "upload_validator_tests"


def local_validator_tests(problem_dir: Path) -> list[ValidatorTest]:
    """The tests of `validator_tests.yaml` in their order on Polygon. `PolygonStepError` — the
    file or the test groups of `constraints.yaml` don't parse."""
    groups, errors = load_test_groups(Path(problem_dir))
    if errors:
        raise PolygonStepError("; ".join(errors))
    names = [group.name for group in groups] if groups else None
    try:
        return load_validator_tests(Path(problem_dir) / VALIDATOR_TESTS_FILENAME, names)
    except ValidatorTestsError as exc:
        raise PolygonStepError(str(exc)) from exc


def _normalized(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def validator_test_digest(input_text: str, verdict: str, group: str | None) -> str:
    """The hash of a validator test as Polygon has it — shared by push and pull."""
    return digest(json.dumps([_normalized(input_text), verdict, group or ""]))


def local_items(tests: list[ValidatorTest]) -> dict[str, str]:
    """The base keys of the local tests -> their hashes."""
    return {
        validator_test_key(number): validator_test_digest(test.input, test.verdict, test.group)
        for number, test in enumerate(tests, start=1)
    }


def remote_items(remote: list[dict]) -> dict[str, str]:
    """The base keys of the tests of `problem.validatorTests` -> their hashes."""
    return {
        validator_test_key(int(test["index"])): validator_test_digest(
            str(test.get("input") or ""),
            str(test.get("expectedVerdict") or ""),
            str(test["group"]) if test.get("group") not in (None, "") else None,
        )
        for test in remote
    }


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    tests = local_validator_tests(Path(ctx.problems_dir) / ctx.problem_id)
    if not tests:
        return StepResult(
            f"no {VALIDATOR_TESTS_FILENAME} — nothing to upload (it is written by 'llm generate')"
        )
    polygon_id = require_polygon_state(ctx).polygon_id
    items = local_items(tests)
    synced = ctx.synced or {}
    based_extra = [
        key for key in synced if key.startswith(VALIDATOR_TESTS_KEY_PREFIX) and key not in items
    ]
    if not changed_items(ctx, items) and not based_extra:
        return StepResult(NO_CHANGES_MESSAGE)

    remote = remote_items(
        client.call("problem.validatorTests", {"problemId": str(polygon_id)}) or []
    )
    uploaded: dict[str, str] = {}
    for number, test in enumerate(tests, start=1):
        key = validator_test_key(number)
        if remote.get(key) == items[key]:
            continue
        params = {
            "problemId": str(polygon_id),
            "testIndex": str(number),
            "testInput": test.input,
            "testVerdict": test.verdict,
        }
        if test.group is not None:
            params["testGroup"] = test.group
        client.call("problem.saveValidatorTest", params)
        uploaded[key] = items[key]
    extra = sorted(
        int(key.removeprefix(VALIDATOR_TESTS_KEY_PREFIX)) for key in remote if key not in items
    )
    notice = (
        f"validator tests {', '.join(map(str, extra))} on Polygon are not local — the API "
        "can't delete them, delete them on Polygon by hand"
        if extra
        else ""
    )
    message = (
        f"saved {len(uploaded)} of {len(tests)} validator test(s) on Polygon id={polygon_id}"
        if uploaded
        else NO_CHANGES_MESSAGE
    )
    return StepResult(message, uploaded, notice)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base: no validator tests in the base and no local file — the step has
    nothing to do ("done")."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    problem_dir = Path(problems_dir) / problem_id
    if not (problem_dir / VALIDATOR_TESTS_FILENAME).is_file() and not any(
        key.startswith(VALIDATOR_TESTS_KEY_PREFIX) for key in synced
    ):
        return STATUS_DONE, "no validator tests"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=VALIDATOR_TESTS_KEY_PREFIX,
        read_items=lambda: local_items(local_validator_tests(problem_dir)),
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
