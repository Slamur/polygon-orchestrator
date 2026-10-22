"""Общее для тестов polygon-шагов: прогон шага так, как его прогоняет
`run_polygon_pipeline`, — с базой синхронизации и записью загруженного."""

from pathlib import Path

from orchestrator.polygon.state import load_polygon_state, update_sync
from orchestrator.polygon.steps.base import PolygonStep, StepResult, run_step


def run_and_record(
    step: PolygonStep,
    problem_id: str,
    spec,
    outputs_dir: Path,
    client,
    *,
    incremental: bool = False,
) -> StepResult:
    """`incremental=False` — без базы (загрузить всё), как первый push;
    `True` — с базой из `polygon_state.json`. Загруженное записывается в
    базу, как после коммита."""
    synced = None
    if incremental:
        state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
        synced = dict(state.synced) if state is not None else {}
    result = run_step(step, problem_id, spec, outputs_dir=outputs_dir, client=client, synced=synced)
    if result.uploaded:
        update_sync(problem_id, synced=result.uploaded, revision=1, outputs_dir=outputs_dir)
    return result
