"""Polygon step 5 — assigning the problem's checker (`problem.setChecker`).

- `checker.custom_needed: true` — `problems/<problem_id>/checker.cpp`
  (the result of the `checker_draft` generative step) is uploaded as a
  source file (`problem.saveFile`, `type=source`) and assigned as the checker.
- `checker.custom_needed: false` — `problem.setChecker` is called with the name
  of the standard checker from `checker.standard`, without uploading a file
  (the standard checkers are already built into Polygon). In the spec the name is written short
  (`ncmp`), Polygon expects `std::ncmp.cpp` — see `_polygon_standard_name`.

Spec validation (`Checker._check_custom_notes` in `orchestrator/spec.py`) doesn't
require `checker.standard` with `custom_needed: false` — such a spec passes
`load_spec`. Here it's caught with an explicit `PolygonStepError` before the first network
call, rather than going to the API as `None`. The place for the real fix is
`spec.py`, it's a separate task.

Logically the step goes after `upload_validator`, but technically it doesn't
depend on it — nothing of what that one writes is read here.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

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
from orchestrator.polygon.sync import CHECKER_FILE_KEY, CHECKER_KEY, digest
from orchestrator.problem_paths import problem_address

STEP_NAME = "upload_checker"

_CHECKER_FILENAME = "checker.cpp"

_STANDARD_PREFIX = "std::"
_STANDARD_SUFFIX = ".cpp"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Assigns the checker (custom or standard, by `ctx.spec.checker`) if
    it differs from the sync base. For a custom one, a difference of the file or of the
    assigned checker means both calls, as in `upload_validator`."""
    polygon_id = require_polygon_state(ctx).polygon_id

    if ctx.spec.checker.custom_needed:
        content = _read_custom_checker(ctx.problem_id, ctx.problems_dir)
        items = _custom_items(content)
        if not changed_items(ctx, items):
            return StepResult(NO_CHANGES_MESSAGE)
        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "source",
                "name": _CHECKER_FILENAME,
                "file": content,
            },
        )
        client.call(
            "problem.setChecker",
            {"problemId": str(polygon_id), "checker": _CHECKER_FILENAME},
        )
        return StepResult(
            f"uploaded and set custom {_CHECKER_FILENAME} as checker on Polygon id={polygon_id}",
            items,
        )

    standard = _require_standard_name(ctx)
    items = {CHECKER_KEY: digest(standard)}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)
    client.call("problem.setChecker", {"problemId": str(polygon_id), "checker": standard})
    return StepResult(f"set standard checker {standard!r} on Polygon id={polygon_id}", items)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base. The spec isn't available here, so custom or
    standard — by the base: if it has `checker.cpp` — the local
    `checker.cpp` is compared, otherwise the standard checker is considered "done"
    (a change of `checker.standard` in the spec isn't visible from here)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    if CHECKER_FILE_KEY not in synced and CHECKER_KEY in synced:
        return STATUS_DONE, "standard checker"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=CHECKER_KEY,
        read_items=lambda: _custom_items(_read_custom_checker(problem_id, problems_dir)),
    )


def _custom_items(content: bytes) -> dict[str, str]:
    return {CHECKER_FILE_KEY: digest(content), CHECKER_KEY: digest(_CHECKER_FILENAME)}


def _read_custom_checker(problem_id: str, problems_dir: Path) -> bytes:
    checker_path = Path(problems_dir) / problem_id / _CHECKER_FILENAME
    if not checker_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': checker.custom_needed=true, but "
            f"{checker_path} not found — first run the generative step checker_draft "
            f"(`orchestrator {address} llm generate --step checker_draft`)"
        )
    return checker_path.read_bytes()


def _require_standard_name(ctx: StepContext) -> str:
    standard = ctx.spec.checker.standard
    if not (standard and standard.strip()):
        raise PolygonStepError(
            f"'{ctx.problem_id}': checker.custom_needed=false, but "
            "checker.standard is not set in the spec — nothing to pass to "
            "problem.setChecker (spec validation does not catch this, see "
            "the upload_checker.py docstring); set checker.standard, "
            "e.g. ncmp"
        )
    return _polygon_standard_name(standard.strip())


def _polygon_standard_name(standard: str) -> str:
    """`ncmp` -> `std::ncmp.cpp`; an already full name (`std::ncmp.cpp`) — as is."""
    name = standard if standard.startswith(_STANDARD_PREFIX) else _STANDARD_PREFIX + standard
    return name if name.endswith(_STANDARD_SUFFIX) else name + _STANDARD_SUFFIX


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
