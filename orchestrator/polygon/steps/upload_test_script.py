"""A Polygon step — uploading the FreeMarker test-script (the result of the generative
step `test_script_draft`) to Polygon (`problem.saveScript`,
`testset=tests`).

The path to the script file is taken from
`orchestrator.steps.test_script_draft.primary_artifact_path` — the single
source of truth for the name (`test_script`/`test_script_groups` by
`generation.script_style`), the flat/groups branching isn't duplicated here.

An MVP limitation: `generation.script_style == "groups"` is deliberately
rejected with `PolygonStepError` before any other logic — the groups mode in
Polygon additionally requires `problem.enableGroups` (not designed), and
there are no groups templates in `templates/` now. An explicit error is better than quiet
wrong behavior.

When saving a script, Polygon drops its empty lines, so the
hash is computed from `normalize_script` (without empty lines and trailing spaces on
lines) — otherwise `polygon pull` would see a mismatch with the local file and
reset the base, and the next push would upload the script again. What's uploaded
is the original file as is.

Before uploading, the script is expanded locally and checked for identical
lines and lines with quotes or an extra `>` in the generator's parameters
(`orchestrator/checks/test_script_check.py`, `local check` does the same):
Polygon won't accept such a script or won't generate tests from it, so the
step fails before going to the network, with a report on which script lines
printed them. If
the check can't be run (no `java`, FreeMarker didn't download) or
FreeMarker didn't expand the script — only a warning to the log: the local
expansion may diverge from Polygon, and it doesn't block the upload.
`ORCHESTRATOR_SKIP_SCRIPT_CHECK=1` turns the check off completely.

Having saved the script, Polygon immediately generates tests from it, and committing
the working copy before the generation ends is pointless. So the step goes last in push,
`problem.saveScript` waits for a response for `_SAVE_SCRIPT_TIMEOUT_SECONDS`
(on a large test set — longer than the client's usual timeout; the request
can't be repeated — the generation would start again), and then the step polls
`problem.tests` with a growing pause until the `scriptLine`s of the generated tests
match, in order, the commands of the expanded script (a script line without
`> $`) — no longer than `_GENERATION_WAIT_SECONDS`. They didn't match — `PolygonStepError`
without a commit: the uploaded script stays in the Polygon working copy, and the next
push stops on uncommitted edits. If the script isn't expanded locally
(the check is off or unavailable), the sign of the generation's end is only the response
to `problem.saveScript`: it came — the step passes, it didn't — `PolygonStepError`.

Logically the step goes after `upload_generators` — the script refers to the
generators by name — even though Polygon doesn't check this when saving the
script.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import itertools
import logging
import os
import time
from pathlib import Path

import requests

from orchestrator.checks.test_script_check import (
    SKIP_CHECK_ENV,
    ScriptCheckUnavailable,
    ScriptRenderError,
    check_script,
)
from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import SCRIPT_KEY, digest
from orchestrator.problem_paths import problem_address
from orchestrator.steps.test_script_draft import (
    primary_artifact_path as script_artifact_path,
)

STEP_NAME = "upload_test_script"

logger = logging.getLogger(__name__)

_TESTSET = "tests"

# How long to wait for the `problem.saveScript` response: Polygon responds only
# after generating the tests (see the module docstring).
_SAVE_SCRIPT_TIMEOUT_SECONDS = 600

# Waiting for the end of test generation: polling `problem.tests` with a pause growing
# twofold from the first to the maximal one, until the overall limit runs out.
_GENERATION_POLL_FIRST_DELAY_SECONDS = 5
_GENERATION_POLL_MAX_DELAY_SECONDS = 120
_GENERATION_WAIT_SECONDS = 1800

# The possible names of the script file — for `_compute_status`, where there's no spec.
SCRIPT_FILENAMES = ("test_script", "test_script_groups")


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads the test-script if it differs from the sync base.

    All three checks (`script_style != "groups"`, `create_problem`,
    the script file's presence) — before the first network call.
    """
    if ctx.spec.generation.script_style == "groups":
        raise PolygonStepError(
            f"'{ctx.problem_id}': script_style='groups' is not supported by this "
            "step (requires a separate problem.enableGroups, not implemented) — "
            "see the upload_test_script.py docstring"
        )

    polygon_id = require_polygon_state(ctx).polygon_id
    script_path = script_artifact_path(ctx.problem_id, ctx.spec, problems_dir=ctx.problems_dir)
    content = _read_script(ctx.problem_id, ctx.problems_dir, script_path)
    items = {SCRIPT_KEY: digest(normalize_script(content))}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    expected = _check_script_lines(ctx.problem_id, script_path.name, content)
    timed_out = _save_script(ctx.problem_id, polygon_id, content, client)
    waited = _wait_for_tests(ctx.problem_id, polygon_id, expected, timed_out, client)
    return StepResult(
        f"uploaded test script ({script_path.name}) to Polygon id={polygon_id}{waited}", items
    )


def _save_script(problem_id: str, polygon_id: int, content: bytes, client: PolygonClient) -> bool:
    """`problem.saveScript`; `True` — the response didn't come in time (the tests are most
    likely still being generated, see the module docstring)."""
    try:
        client.call(
            "problem.saveScript",
            {"problemId": str(polygon_id), "testset": _TESTSET, "source": content.decode("utf-8")},
            timeout=_SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
    except requests.ReadTimeout:
        logger.warning(
            "'%s': no response to problem.saveScript in %ss — Polygon is probably still "
            "generating tests",
            problem_id,
            _SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
        return True
    return False


def _wait_for_tests(
    problem_id: str,
    polygon_id: int,
    expected: list[str] | None,
    timed_out: bool,
    client: PolygonClient,
) -> str:
    """Waits until the generated tests on Polygon match the expanded
    script (see the module docstring); returns a postscript to the step's message.

    `expected` — `None` if the script isn't expanded locally: the response to
    `problem.saveScript` is then the only sign of the generation's end, and without
    it the step fails without committing undergenerated tests.
    """
    if expected is None:
        if timed_out:
            raise PolygonStepError(
                f"'{problem_id}': no response to problem.saveScript in "
                f"{_SAVE_SCRIPT_TIMEOUT_SECONDS}s and the script was not expanded locally, so "
                "the end of test generation cannot be checked — wait for it on Polygon, commit "
                "there, then run `polygon pull`"
            )
        return ""

    started = time.monotonic()
    for attempt in itertools.count():
        generated = _generated_script_lines(polygon_id, client)
        if generated == expected:
            break
        elapsed = time.monotonic() - started
        if elapsed >= _GENERATION_WAIT_SECONDS:
            raise PolygonStepError(
                f"'{problem_id}': tests on Polygon id={polygon_id} still do not match the script "
                f"after {int(elapsed)}s ({_progress(generated, expected)}) — check test "
                "generation on Polygon, commit there, then run `polygon pull`"
            )
        delay = min(
            _GENERATION_POLL_FIRST_DELAY_SECONDS * 2**attempt, _GENERATION_POLL_MAX_DELAY_SECONDS
        )
        logger.info(
            "'%s': waiting for test generation (%s), next check in %ss",
            problem_id,
            _progress(generated, expected),
            delay,
        )
        time.sleep(delay)
    if not attempt:
        return ""
    return f"; waited {int(time.monotonic() - started)}s for test generation"


def _generated_script_lines(polygon_id: int, client: PolygonClient) -> list[str] | None:
    """The `scriptLine`s of the generated tests in order; `None` if Polygon didn't
    respond in time (busy with generation)."""
    try:
        tests = client.call(
            "problem.tests",
            {"problemId": str(polygon_id), "testset": _TESTSET, "noInputs": "true"},
        )
    except requests.ReadTimeout:
        return None
    return [_normalize_command(t.get("scriptLine", "")) for t in tests if not t.get("manual")]


def _progress(generated: list[str] | None, expected: list[str]) -> str:
    if generated is None:
        return "Polygon did not respond"
    matched = sum(1 for g, e in zip(generated, expected, strict=False) if g == e)
    return f"{matched}/{len(expected)} tests match the script"


def _normalize_command(line: str) -> str:
    """The generator command as a Polygon test's `scriptLine` shows it: without
    the `> $` redirection and with single spaces."""
    return " ".join(line.split(">", 1)[0].split())


def _check_script_lines(problem_id: str, script_name: str, content: bytes) -> list[str] | None:
    """`PolygonStepError` with a report if the expanded script has identical
    lines or lines Polygon won't accept (see the module docstring).

    Returns the commands of the expanded script (`_normalize_command`) — by them
    `_wait_for_tests` waits for the generation's end; `None` if the script isn't expanded.
    """
    if os.environ.get(SKIP_CHECK_ENV):
        return None
    try:
        result = check_script(content.decode("utf-8"), script_name)
    except (ScriptCheckUnavailable, ScriptRenderError) as exc:
        logger.warning("'%s': line check of %s skipped — %s", problem_id, script_name, exc)
        return None
    if result.failed:
        raise PolygonStepError(
            f"'{problem_id}': {result.report}\n(set {SKIP_CHECK_ENV}=1 to upload anyway)"
        )
    return [_normalize_command(line.text) for line in result.lines]


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`). The spec isn't available here,
    so the first existing one of `SCRIPT_FILENAMES` is compared."""

    def read_items() -> dict[str, str]:
        problem_dir = Path(problems_dir) / problem_id
        existing = [name for name in SCRIPT_FILENAMES if (problem_dir / name).exists()]
        script_path = problem_dir / (existing[0] if existing else SCRIPT_FILENAMES[0])
        content = _read_script(problem_id, problems_dir, script_path)
        return {SCRIPT_KEY: digest(normalize_script(content))}

    return sync_status(problem_id, problems_dir, key_prefix=SCRIPT_KEY, read_items=read_items)


def normalize_script(content: bytes) -> bytes:
    """The script in the form in which `push` and `pull` compare it: without empty
    lines (Polygon drops them), without trailing spaces on lines and with LF instead of
    CRLF."""
    lines = content.decode("utf-8").replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines if line.strip()).encode("utf-8")


def _read_script(problem_id: str, problems_dir: Path, script_path: Path) -> bytes:
    """The test-script's contents. The path is passed from outside: in `_execute` it
    depends on the spec's `script_style` (`primary_artifact_path`), and in
    `_compute_status` there's no spec."""
    if not script_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': {script_path} not found — first run "
            f"the generative step test_script_draft (`orchestrator {address} llm generate "
            f"--step test_script_draft`)"
        )
    return script_path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
