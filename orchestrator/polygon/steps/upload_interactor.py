"""A Polygon step — uploading the interactor of an interactive problem
(`problem.setInteractor`).

A problem is interactive if the spec has an `interactor` section: then
`problems/<problem_id>/interactor.cpp` (the result of the `interactor_draft` generative step)
is uploaded as a source file (`problem.saveFile`,
`type=source`) and assigned as the interactor. For a normal problem the step does
nothing. The problem's interactivity flag is set by `set_constraints` — it goes
earlier, and by this step Polygon already knows the problem is interactive.

If the problem was made normal and the interactor stayed on Polygon, the step doesn't
touch it (just as other steps don't delete files on Polygon).

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
from orchestrator.polygon.sync import (
    INTERACTOR_FILE_KEY,
    INTERACTOR_FILENAME,
    INTERACTOR_KEY,
    digest,
)
from orchestrator.problem_paths import problem_address

STEP_NAME = "upload_interactor"

NOT_INTERACTIVE_MESSAGE = (
    "the problem is not interactive (no 'interactor' section in the spec), skipped"
)


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads and assigns `interactor.cpp` if the file or the assigned
    interactor differ from the sync base — both calls, as in
    `upload_validator`."""
    if not ctx.spec.interactive:
        return StepResult(NOT_INTERACTIVE_MESSAGE)
    polygon_id = require_polygon_state(ctx).polygon_id
    content = _read_interactor(ctx.problem_id, ctx.problems_dir)
    items = _items(content)
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)
    client.call(
        "problem.saveFile",
        {
            "problemId": str(polygon_id),
            "type": "source",
            "name": INTERACTOR_FILENAME,
            "file": content,
        },
    )
    client.call(
        "problem.setInteractor",
        {"problemId": str(polygon_id), "interactor": INTERACTOR_FILENAME},
    )
    return StepResult(
        f"uploaded and set {INTERACTOR_FILENAME} as interactor on Polygon id={polygon_id}",
        items,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base. The spec isn't available here, so whether the problem
    is interactive — by the base and the file: there's neither an interactor in the base nor a local
    `interactor.cpp` — the problem is normal, the step has nothing to do ("done")."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = state.synced if state is not None else {}
    local = Path(problems_dir) / problem_id / INTERACTOR_FILENAME
    if INTERACTOR_FILE_KEY not in synced and not local.exists():
        return STATUS_DONE, "not interactive"
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=INTERACTOR_KEY,
        read_items=lambda: _items(_read_interactor(problem_id, problems_dir)),
    )


def _items(content: bytes) -> dict[str, str]:
    return {INTERACTOR_FILE_KEY: digest(content), INTERACTOR_KEY: digest(INTERACTOR_FILENAME)}


def _read_interactor(problem_id: str, problems_dir: Path) -> bytes:
    path = Path(problems_dir) / problem_id / INTERACTOR_FILENAME
    if not path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': the problem is interactive, but {path} not found — "
            "first run the generative step interactor_draft "
            f"(`orchestrator {address} llm generate --step interactor_draft`)"
        )
    return path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
