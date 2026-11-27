"""A Polygon step — the checker tests (`problem.saveCheckerTest`), after `upload_checker`.

Only for a custom checker (`checker.custom_needed: true`) of a problem that isn't
interactive — the same condition as the `checker_tests` generative step; otherwise the step
does nothing. The tests are `checker_tests.auto.yaml` (the tests of the examples, written by
the local run `run_checker_tests`) and then `checker_tests.yaml` (the `checker_tests` step),
in their order: test K on Polygon is the K-th of this list (`local_checker_tests`). Neither
file — nothing to upload.

What counts as changed — a sync base key per test, `checker_tests/<K>`
(`checker_test_digest`: the input, output, answer and verdict, line endings and trailing
spaces dropped — so a test Polygon stores with `\\r\\n` is the same). A test that differs
from the base is saved over the K-th test on Polygon (`testIndex=K`); the verdict `FAIL` is
Polygon's `CRASHED`. When something changed, the step reads `problem.checkerTests` first and
saves only the tests that differ from what Polygon has. Polygon tests after the last local
one stay: the API has no method to delete a checker test — the step says which ones (the
`notice`), the author deletes them by hand.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.checks.checker_tests import (
    AUTO_CHECKER_TESTS_FILENAME,
    CHECKER_TESTS_FILENAME,
    VERDICT_FAIL,
    VERDICT_OK,
    VERDICT_PE,
    VERDICT_WA,
    CheckerTest,
    CheckerTestsError,
    load_checker_tests,
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
from orchestrator.polygon.sync import CHECKER_TESTS_KEY_PREFIX, checker_test_key, digest

STEP_NAME = "upload_checker_tests"

# The local verdict -> Polygon's (`testVerdict`, `expectedVerdict`).
POLYGON_VERDICTS = {
    VERDICT_OK: "OK",
    VERDICT_WA: "WRONG_ANSWER",
    VERDICT_PE: "PRESENTATION_ERROR",
    VERDICT_FAIL: "CRASHED",
}

NOT_NEEDED_MESSAGE = "no checker tests: a standard checker or an interactive problem, skipped"


def local_checker_tests(problem_dir: Path) -> list[CheckerTest]:
    """The tests in their order on Polygon: the auto file, then the step's.
    `PolygonStepError` — a file doesn't parse."""
    tests: list[CheckerTest] = []
    for name in (AUTO_CHECKER_TESTS_FILENAME, CHECKER_TESTS_FILENAME):
        try:
            tests += load_checker_tests(Path(problem_dir) / name)
        except CheckerTestsError as exc:
            raise PolygonStepError(str(exc)) from exc
    return tests


def _normalized(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def checker_test_digest(input_text: str, output: str, answer: str, polygon_verdict: str) -> str:
    """The hash of a checker test as Polygon has it — shared by push and pull."""
    texts = [_normalized(text) for text in (input_text, output, answer)]
    return digest(json.dumps([*texts, polygon_verdict]))


def local_items(tests: list[CheckerTest]) -> dict[str, str]:
    """The base keys of the local tests -> their hashes."""
    return {
        checker_test_key(number): checker_test_digest(
            test.input, test.output, test.answer, POLYGON_VERDICTS[test.verdict]
        )
        for number, test in enumerate(tests, start=1)
    }


def remote_items(remote: list[dict]) -> dict[str, str]:
    """The base keys of the tests of `problem.checkerTests` -> their hashes."""
    return {
        checker_test_key(int(test["index"])): checker_test_digest(
            str(test.get("input") or ""),
            str(test.get("output") or ""),
            str(test.get("answer") or ""),
            str(test.get("expectedVerdict") or ""),
        )
        for test in remote
    }


def _needed(ctx: StepContext) -> bool:
    return ctx.spec.checker.custom_needed is True and not ctx.spec.interactive


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    if not _needed(ctx):
        return StepResult(NOT_NEEDED_MESSAGE)
    tests = local_checker_tests(Path(ctx.problems_dir) / ctx.problem_id)
    if not tests:
        return StepResult(
            f"no {AUTO_CHECKER_TESTS_FILENAME} or {CHECKER_TESTS_FILENAME} — nothing to upload "
            "(they are written by 'llm generate')"
        )
    polygon_id = require_polygon_state(ctx).polygon_id
    items = local_items(tests)
    synced = ctx.synced or {}
    based_extra = [
        key for key in synced if key.startswith(CHECKER_TESTS_KEY_PREFIX) and key not in items
    ]
    if not changed_items(ctx, items) and not based_extra:
        return StepResult(NO_CHANGES_MESSAGE)

    remote = remote_items(client.call("problem.checkerTests", {"problemId": str(polygon_id)}) or [])
    uploaded: dict[str, str] = {}
    for number, test in enumerate(tests, start=1):
        key = checker_test_key(number)
        if remote.get(key) == items[key]:
            continue
        client.call(
            "problem.saveCheckerTest",
            {
                "problemId": str(polygon_id),
                "testIndex": str(number),
                "testInput": test.input,
                "testOutput": test.output,
                "testAnswer": test.answer,
                "testVerdict": POLYGON_VERDICTS[test.verdict],
            },
        )
        uploaded[key] = items[key]
    extra = sorted(
        int(key.removeprefix(CHECKER_TESTS_KEY_PREFIX)) for key in remote if key not in items
    )
    notice = (
        f"checker tests {', '.join(map(str, extra))} on Polygon are not local — the API "
        "can't delete them, delete them on Polygon by hand"
        if extra
        else ""
    )
    message = (
        f"saved {len(uploaded)} of {len(tests)} checker test(s) on Polygon id={polygon_id}"
        if uploaded
        else NO_CHANGES_MESSAGE
    )
    return StepResult(message, uploaded, notice)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base. The spec isn't available here: no checker tests in the base and
    no local files — the step has nothing to do ("done")."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    problem_dir = Path(problems_dir) / problem_id
    has_files = any(
        (problem_dir / name).is_file()
        for name in (AUTO_CHECKER_TESTS_FILENAME, CHECKER_TESTS_FILENAME)
    )
    if not has_files and not any(key.startswith(CHECKER_TESTS_KEY_PREFIX) for key in synced):
        return STATUS_DONE, "no checker tests"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=CHECKER_TESTS_KEY_PREFIX,
        read_items=lambda: local_items(local_checker_tests(problem_dir)),
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
