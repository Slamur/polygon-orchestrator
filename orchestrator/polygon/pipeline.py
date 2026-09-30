"""Прогон polygon-шагов для одного `problem_id` (CLAUDE.md, "Скоуп
(текущий)": создание задачи на Polygon и последующие шаги интеграции).

Аналог `orchestrator/pipeline.py`, но сознательно без кэша: нет
`input_hash`/`prompt_hash`/`is_cache_valid`, `run_polygon_pipeline` вызывает
каждый шаг всегда. Идемпотентность — забота самого шага (`check_done` в
`PolygonStep`, см. `steps/base.py`), а не пайплайна.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator.cache import OUTPUTS_DIR
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.steps import create_problem, set_constraints
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.spec import SpecValidationError, load_spec

SPECS_DIR = Path("specs")

# Список расширяется по мере добавления шагов (save_statement, ...) — дописывать новые имена в конец и добавлять запись в
# `_POLYGON_STEPS`; больше ничего менять не нужно, если новый шаг — такой же
# `PolygonStep`, запускаемый через `steps.base.run_step`.
POLYGON_STEP_ORDER: list[str] = [create_problem.STEP_NAME, set_constraints.STEP_NAME]

_POLYGON_STEPS: dict[str, PolygonStep] = {
    create_problem.STEP_NAME: create_problem.STEP,
    set_constraints.STEP_NAME: set_constraints.STEP,
}

# Статусы PolygonStepOutcome.status:
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"


@dataclass
class PolygonStepOutcome:
    """Что произошло с одним polygon-шагом (или его состояние для `status`)."""

    step_name: str
    status: str
    detail: str = ""


@dataclass
class PolygonPipelineResult:
    """Результат прогона polygon-шагов для одного `problem_id`."""

    problem_id: str
    outcomes: list[PolygonStepOutcome] = field(default_factory=list)
    spec_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.spec_error is None and all(
            o.status != OUTCOME_ERROR for o in self.outcomes
        )


def run_polygon_pipeline(
    problem_id: str,
    *,
    only_step: Optional[str] = None,
    specs_dir: Path = SPECS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    client: PolygonClient | None = None,
) -> PolygonPipelineResult:
    """Прогоняет polygon-шаги для `problem_id` по порядку `POLYGON_STEP_ORDER`
    (или только один, если задан `only_step`).

    Кэша нет: каждый шаг вызывается всегда (что он при этом делает, если уже
    выполнялся раньше, решает его собственный `check_done`).

    `client` создаётся один раз здесь и передаётся во все шаги — чтобы не
    плодить по отдельному `PolygonClient` с ленивым чтением `.env` на шаг.

    Останавливается на первой `PolygonApiError` (ошибка Polygon API) или
    `PolygonStepError` (не хватает локальных данных/зависимостей шага), не
    бросая её наружу:
    `result.outcomes` получает запись со статусом "error", последующие шаги
    не запускаются. Ошибка валидации спека — как в `run_pipeline`:
    записывается в `result.spec_error`, клиент при этом не создаётся.
    """
    try:
        spec = load_spec(Path(specs_dir) / f"{problem_id}.yaml")
    except SpecValidationError as exc:
        return PolygonPipelineResult(problem_id=problem_id, spec_error=str(exc))

    client = client or PolygonClient()
    result = PolygonPipelineResult(problem_id=problem_id)
    steps_to_run = POLYGON_STEP_ORDER if only_step is None else [only_step]

    for step_name in steps_to_run:
        step = _POLYGON_STEPS[step_name]
        try:
            detail = run_step(step, problem_id, spec, outputs_dir=outputs_dir, client=client)
        except (PolygonApiError, PolygonStepError) as exc:
            result.outcomes.append(PolygonStepOutcome(step_name, OUTCOME_ERROR, str(exc)))
            return result
        result.outcomes.append(PolygonStepOutcome(step_name, OUTCOME_OK, detail))

    return result


def compute_polygon_step_statuses(
    problem_id: str, *, outputs_dir: Path = OUTPUTS_DIR
) -> list[PolygonStepOutcome]:
    """Состояние polygon-шагов для `orchestrator polygon <id> status`, без сети:
    по `compute_status` каждого шага, в порядке `POLYGON_STEP_ORDER`.

    Состояние — только по локальным данным (`polygon_state.json` и входные
    файлы шага); ручные правки на стороне Polygon отсюда не видны.
    """
    outcomes = []
    for step_name in POLYGON_STEP_ORDER:
        status, detail = _POLYGON_STEPS[step_name].compute_status(problem_id, Path(outputs_dir))
        outcomes.append(PolygonStepOutcome(step_name, status, detail))
    return outcomes
