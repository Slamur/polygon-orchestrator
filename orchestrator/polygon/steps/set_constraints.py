"""Polygon step 2 — transferring the actual TL/ML from `problems/<problem_id>/limits.yaml`
(see `orchestrator/limits.py`; the `limits_from_constraints` step derives it from
`constraints.yaml`) to Polygon via `problem.updateInfo` together with the
problem's interactivity flag (whether the spec has an `interactor` section).

The flag is a separate element of the sync base (`INTERACTIVE_KEY`), but
it's compared only if the problem is interactive or the flag is already in the base:
normal problems synced before it appeared don't have it, and push mustn't
send `problem.updateInfo` again because of that.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits
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
from orchestrator.polygon.sync import (
    INTERACTIVE_KEY,
    LIMITS_KEY,
    interactive_digest,
    limits_digest,
)
from orchestrator.problem_paths import problem_address

STEP_NAME = "set_constraints"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Transfers the TL/ML and the interactivity flag to Polygon if they differ
    from the sync base (`problem.updateInfo` gets them together)."""
    polygon_id = require_polygon_state(ctx).polygon_id
    time_limit_ms, memory_limit_mb = read_limits(ctx.problem_id, ctx.problems_dir)
    interactive = ctx.spec.interactive
    items = {LIMITS_KEY: limits_digest(time_limit_ms, memory_limit_mb)}
    if interactive or INTERACTIVE_KEY in (ctx.synced or {}):
        items[INTERACTIVE_KEY] = interactive_digest(interactive)
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    params = {
        "problemId": str(polygon_id),
        "timeLimit": str(time_limit_ms),
        "memoryLimit": str(memory_limit_mb),
        "inputFile": "",
        "outputFile": "",
        "interactive": str(interactive).lower(),
    }
    client.call("problem.updateInfo", params)
    return StepResult(
        f"set {_describe(time_limit_ms, memory_limit_mb)} interactive={params['interactive']} "
        f"on Polygon id={polygon_id}",
        items,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`). Manual TL/ML edits in the Polygon UI
    aren't visible from here; neither is the interactivity flag (the spec isn't
    available here, push notices a change of the flag in the spec)."""

    def read_items() -> dict[str, str]:
        return {LIMITS_KEY: limits_digest(*read_limits(problem_id, problems_dir))}

    return sync_status(problem_id, problems_dir, key_prefix=LIMITS_KEY, read_items=read_items)


def _describe(time_limit_ms: int, memory_limit_mb: int) -> str:
    return f"timeLimit={time_limit_ms}ms memoryLimit={memory_limit_mb}MB"


def read_limits(problem_id: str, problems_dir: Path) -> tuple[int, int]:
    """(timeLimit in milliseconds, memoryLimit in megabytes) from
    `limits.yaml` — in the units of `problem.updateInfo` (`limits.parse_limits`).
    Without the network; if `limits.yaml` doesn't exist or is invalid — `PolygonStepError`
    (if it's missing — saying how to get it). `polygon pull` needs it too
    — to compare the local TL/ML with Polygon.

    `problem.updateInfo` also gets inputFile="", outputFile="" (see
    `_execute`): the spec format (docs/SPEC_FORMAT.md) has no fields for
    file input/output, the values reflect the stdin/stdout default. If
    such fields appear — that's where to change it.

    NOTE: the documentation text of `problem.updateInfo` doesn't name the units
    explicitly — after the first real call it's worth checking against the
    General tab in the Polygon UI.
    """
    limits_path = Path(problems_dir) / problem_id / LIMITS_FILENAME
    if not limits_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': {limits_path} not found — derive it from constraints.yaml "
            f"(`orchestrator {address} llm generate --step limits_from_constraints`, "
            "the model is not called) or take it from Polygon "
            f"(`orchestrator {address} polygon pull`)"
        )
    try:
        return parse_limits(limits_path.read_text(encoding="utf-8"))
    except LimitsError as exc:
        raise PolygonStepError(f"'{problem_id}': {limits_path}: {exc}") from exc


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
