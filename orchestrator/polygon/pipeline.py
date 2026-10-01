"""Прогон polygon-шагов для одного `problem_id` (CLAUDE.md, "Скоуп
(текущий)": создание задачи на Polygon и последующие шаги интеграции).

Аналог `orchestrator/pipeline.py`, но сознательно без кэша: нет
`input_hash`/`prompt_hash`/`is_cache_valid`, `run_polygon_pipeline` вызывает
каждый шаг всегда. Идемпотентность — забота самого шага (`check_done` в
`PolygonStep`, см. `steps/base.py`), а не пайплайна.

После каждого успешного шага (кроме тех, у кого `commits_changes=False`)
рабочая копия задачи коммитится на Polygon (`problem.commitChanges`) — чтобы
при падении на очередном шаге всё сделанное предыдущими уже было в
ревизии, а не висело незакоммиченным.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from orchestrator.cache import OUTPUTS_DIR
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.steps import (
    create_problem,
    set_constraints,
    upload_checker,
    upload_generators,
    upload_problem_lib,
    upload_solutions,
    upload_statement,
    upload_test_script,
    upload_validator,
)
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.spec import SpecValidationError, load_spec

logger = logging.getLogger(__name__)

SPECS_DIR = Path("specs")

# Список расширяется по мере добавления шагов — дописывать новые имена в конец
# и добавлять запись в `_POLYGON_STEPS`; больше ничего менять не нужно, если новый шаг — такой же
# `PolygonStep`, запускаемый через `steps.base.run_step`.
POLYGON_STEP_ORDER: list[str] = [
    create_problem.STEP_NAME,
    set_constraints.STEP_NAME,
    upload_problem_lib.STEP_NAME,
    upload_validator.STEP_NAME,
    upload_checker.STEP_NAME,
    upload_statement.STEP_NAME,
    upload_generators.STEP_NAME,
    upload_test_script.STEP_NAME,
    upload_solutions.STEP_NAME,
]

_POLYGON_STEPS: dict[str, PolygonStep] = {
    create_problem.STEP_NAME: create_problem.STEP,
    set_constraints.STEP_NAME: set_constraints.STEP,
    upload_problem_lib.STEP_NAME: upload_problem_lib.STEP,
    upload_validator.STEP_NAME: upload_validator.STEP,
    upload_checker.STEP_NAME: upload_checker.STEP,
    upload_statement.STEP_NAME: upload_statement.STEP,
    upload_generators.STEP_NAME: upload_generators.STEP,
    upload_test_script.STEP_NAME: upload_test_script.STEP,
    upload_solutions.STEP_NAME: upload_solutions.STEP,
}

# Статусы PolygonStepOutcome.status:
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"

_COMMIT_MESSAGE_PREFIX = "orchestrator: "


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
    on_outcome: Callable[[PolygonStepOutcome], None] | None = None,
) -> PolygonPipelineResult:
    """Прогоняет polygon-шаги для `problem_id` по порядку `POLYGON_STEP_ORDER`
    (или только один, если задан `only_step`).

    Кэша нет: каждый шаг вызывается всегда (что он при этом делает, если уже
    выполнялся раньше, решает его собственный `check_done`).

    После каждого успешного шага с `commits_changes=True` — `problem.commitChanges`
    (см. `_commit_changes`). Ошибка коммита — такая же ошибка шага: шаг
    получает статус "error", пайплайн останавливается; при повторном запуске
    шаг выполнится и закоммитится заново.

    `client` создаётся один раз здесь и передаётся во все шаги — чтобы не
    плодить по отдельному `PolygonClient` с ленивым чтением `.env` на шаг.

    Останавливается на первой ошибке шага, не бросая её наружу:
    `result.outcomes` получает запись со статусом "error", последующие шаги
    не запускаются. Помимо ожидаемых `PolygonApiError` (ошибка Polygon API)
    и `PolygonStepError` (не хватает локальных данных/зависимостей шага)
    так же обрабатывается любое другое исключение (сеть, отсутствие ключей,
    баг в шаге) — с traceback в лог, `detail` при этом — тип и текст
    исключения. Полный текст ошибки всегда пишется в лог (ERROR), т.к.
    вызывающий код может показывать `detail` сокращённо.

    `on_outcome` вызывается сразу после каждого шага (и успешного, и
    упавшего) — чтобы CLI показывал прогресс по мере выполнения, а не
    одним куском в конце. Ошибка валидации спека — как в `run_pipeline`:
    записывается в `result.spec_error`, клиент при этом не создаётся.
    """
    try:
        spec = load_spec(Path(specs_dir) / f"{problem_id}.yaml")
    except SpecValidationError as exc:
        return PolygonPipelineResult(problem_id=problem_id, spec_error=str(exc))

    client = client or PolygonClient()
    result = PolygonPipelineResult(problem_id=problem_id)
    steps_to_run = POLYGON_STEP_ORDER if only_step is None else [only_step]

    def record(outcome: PolygonStepOutcome) -> None:
        result.outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    for step_name in steps_to_run:
        step = _POLYGON_STEPS[step_name]
        logger.info("[%s] start", step_name)
        try:
            detail = run_step(step, problem_id, spec, outputs_dir=outputs_dir, client=client)
            if step.commits_changes:
                detail += "; " + _commit_changes(
                    problem_id, step_name, outputs_dir=outputs_dir, client=client
                )
        except (PolygonApiError, PolygonStepError) as exc:
            logger.error("[%s] %s", step_name, exc)
            record(PolygonStepOutcome(step_name, OUTCOME_ERROR, str(exc)))
            return result
        except Exception as exc:  # noqa: BLE001 — traceback в лог, а не в консоль
            logger.exception("[%s] неожиданная ошибка", step_name)
            record(
                PolygonStepOutcome(step_name, OUTCOME_ERROR, f"{type(exc).__name__}: {exc}")
            )
            return result
        record(PolygonStepOutcome(step_name, OUTCOME_OK, detail))

    return result


def _commit_changes(
    problem_id: str, step_name: str, *, outputs_dir: Path, client: PolygonClient
) -> str:
    """Коммитит рабочую копию задачи на Polygon после шага `step_name`.

    `minorChanges=true` — без email-уведомлений: коммит идёт после каждого
    шага, иначе один прогон рассылал бы письмо на каждый шаг.

    NOTE: поведение `problem.commitChanges` при отсутствии изменений в
    рабочей копии (например, повторный прогон с теми же файлами) на
    реальном API ещё не проверено. Если окажется, что это FAILED, —
    такой ответ нужно здесь распознавать и не считать ошибкой шага.
    """
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': нечего коммитить после {step_name} — задача не "
            "привязана к Polygon (polygon_state.json отсутствует или повреждён)"
        )
    client.call(
        "problem.commitChanges",
        {
            "problemId": str(state.polygon_id),
            "minorChanges": "true",
            "message": _COMMIT_MESSAGE_PREFIX + step_name,
        },
    )
    return f"committed on Polygon id={state.polygon_id}"


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
