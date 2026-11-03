"""Общее для тестов polygon-шагов: синтетическая задача, привязка её к
Polygon и прогон шага так, как его прогоняет `run_polygon_pipeline`, — с
базой синхронизации и записью загруженного."""

from pathlib import Path

from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state, update_sync
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
    """Задача `PROBLEM_ID` привязана к Polygon (`polygon_state.json`)."""
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
    """`incremental=False` — без базы (загрузить всё), как первый push;
    `True` — с базой из `polygon_state.json`. Загруженное записывается в
    базу, как после коммита."""
    synced = None
    if incremental:
        state = load_polygon_state(problem_id, problems_dir=problems_dir)
        synced = dict(state.synced) if state is not None else {}
    result = run_step(step, problem_id, spec, problems_dir=problems_dir, client=client, synced=synced)
    if result.uploaded:
        update_sync(problem_id, synced=result.uploaded, revision=1, problems_dir=problems_dir)
    return result
