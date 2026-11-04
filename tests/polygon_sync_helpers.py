"""Shared by the polygon step tests: a synthetic problem, binding it to
Polygon and running a step the way `run_polygon_pipeline` runs it — with
the sync base and recording what was uploaded."""

from pathlib import Path

from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
    update_sync,
)
from orchestrator.polygon.steps.base import PolygonStep, StepResult, run_step

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242


def link(
    problems_dir: Path,
    *,
    polygon_id: int = POLYGON_ID,
    revision: int | None = None,
    synced: dict[str, str] | None = None,
) -> None:
    """The problem `PROBLEM_ID` is bound to Polygon (`polygon_state.json`)."""
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=polygon_id,
            created_at="2026-09-19T12:00:00+00:00",
            revision=revision,
            synced=synced or {},
        ),
        problems_dir=problems_dir,
    )


def run_and_record(
    step: PolygonStep,
    problem_id: str,
    spec,
    problems_dir: Path,
    client,
    *,
    incremental: bool = False,
) -> StepResult:
    """`incremental=False` — without a base (upload everything), like the first push;
    `True` — with the base from `polygon_state.json`. What was uploaded is recorded in the
    base, as after a commit."""
    synced = None
    if incremental:
        state = load_polygon_state(problem_id, problems_dir=problems_dir)
        synced = dict(state.synced) if state is not None else {}
    result = run_step(
        step, problem_id, spec, problems_dir=problems_dir, client=client, synced=synced
    )
    if result.uploaded:
        update_sync(problem_id, synced=result.uploaded, revision=1, problems_dir=problems_dir)
    return result
