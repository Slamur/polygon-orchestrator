"""Polygon step 4 — uploading `problems/<problem_id>/validator.cpp` (the result of the
`constraints_pick` generative step) to Polygon as a source file
(`problem.saveFile`, `type=source`) and assigning it as the problem's validator
(`problem.setValidator`).

`sourceType` isn't passed in `problem.saveFile` — by the method's description it's
optional, and it's assumed that Polygon determines the language by the
`.cpp` extension. After the first real call it's worth checking in the Files tab of the
Polygon UI that the file has a C++ compiler set; if not — pass
`sourceType` explicitly (take the value from the dropdown in the UI).

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

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
from orchestrator.polygon.sync import VALIDATOR_FILE_KEY, VALIDATOR_KEY, digest
from orchestrator.problem_paths import problem_address

STEP_NAME = "upload_validator"

_VALIDATOR_FILENAME = "validator.cpp"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads `validator.cpp` and assigns it as the validator (`ctx.spec` isn't
    used) if the file or the assigned validator differ from the sync
    base. A difference in either of the two means both calls: after `polygon pull`
    a file with another name may turn out to be assigned, and Polygon may not
    have `validator.cpp` at all."""
    polygon_id = require_polygon_state(ctx).polygon_id
    content = _read_validator(ctx.problem_id, ctx.problems_dir)
    items = _items(content)
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    client.call(
        "problem.saveFile",
        {
            "problemId": str(polygon_id),
            "type": "source",
            "name": _VALIDATOR_FILENAME,
            "file": content,
        },
    )
    client.call(
        "problem.setValidator",
        {"problemId": str(polygon_id), "validator": _VALIDATOR_FILENAME},
    )
    return StepResult(
        f"uploaded and set {_VALIDATOR_FILENAME} as validator on Polygon id={polygon_id}", items
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`)."""
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=VALIDATOR_KEY,
        read_items=lambda: _items(_read_validator(problem_id, problems_dir)),
    )


def _items(content: bytes) -> dict[str, str]:
    return {VALIDATOR_FILE_KEY: digest(content), VALIDATOR_KEY: digest(_VALIDATOR_FILENAME)}


def _read_validator(problem_id: str, problems_dir: Path) -> bytes:
    validator_path = Path(problems_dir) / problem_id / _VALIDATOR_FILENAME
    if not validator_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': {validator_path} not found — first run "
            f"the generative step constraints_pick (`orchestrator {address} llm generate "
            "--step constraints_pick`)"
        )

    return validator_path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
