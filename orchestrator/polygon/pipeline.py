"""Прогон polygon-шагов для одного `problem_id` (CLAUDE.md, "Скоуп
(текущий)": создание задачи на Polygon и последующие шаги интеграции).

Каждый шаг вызывается всегда, но загружает только элементы задачи,
изменившиеся с последней синхронизации (база — `revision`/`synced` в
`polygon_state.json`, см. `orchestrator/polygon/sync.py`). Как
`git push` с проверкой fast-forward: перед первым загружающим шагом
сверяется ревизия задачи на Polygon (`_sync_baseline`):

- ревизия равна записанной и в рабочей копии нет незакоммиченных правок —
  базе можно доверять, шаги загружают только изменённое;
- в рабочей копии есть незакоммиченные правки — push останавливается
  всегда, даже с `force`: иначе коммит после шага унёс бы их в ревизию;
- задан `force` — база не используется, шаги загружают всё (как `git push
  --force`);
- ревизия неизвестна (задача привязана к уже существующей на Polygon или
  до появления этого поля) — что на Polygon, неизвестно, push
  останавливается до `polygon pull` (или `--force`);
- ревизия другая — Polygon меняли в обход оркестратора, push
  останавливается с предложением сделать `polygon pull` или `polygon push
  --force`.

После каждого шага, который что-то загрузил (кроме тех, у кого
`commits_changes=False`), рабочая копия задачи коммитится на Polygon
(`problem.commitChanges`), и только после этого загруженное и новая
ревизия записываются в базу — чтобы при падении на очередном шаге всё
сделанное предыдущими уже было в ревизии и в базе.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from orchestrator.cache import PROBLEMS_DIR
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.steps import (
    create_problem,
    set_constraints,
    upload_checker,
    upload_generators,
    upload_problem_lib,
    upload_solutions,
    upload_spec,
    upload_statement,
    upload_test_script,
    upload_validator,
)
from orchestrator.polygon.state import load_polygon_state, update_sync
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.sync import fetch_revision
from orchestrator.problem_paths import problem_address
from orchestrator.spec import SpecValidationError, load_problem_spec
from orchestrator.step_order import POLYGON_STEP_ORDER

logger = logging.getLogger(__name__)

_POLYGON_STEPS: dict[str, PolygonStep] = {
    create_problem.STEP_NAME: create_problem.STEP,
    upload_spec.STEP_NAME: upload_spec.STEP,
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
    # `detail` — собственный текст оркестратора (`PolygonStepError`): короткий,
    # однострочный, с подсказкой в конце, — CLI печатает его целиком, а не
    # обрезает, как ответы Polygon API и неожиданные исключения.
    full_detail: bool = False


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
    force: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    on_outcome: Callable[[PolygonStepOutcome], None] | None = None,
) -> PolygonPipelineResult:
    """Прогоняет polygon-шаги для `problem_id` по порядку `POLYGON_STEP_ORDER`
    (или только один, если задан `only_step`).

    Каждый шаг вызывается всегда; что загружать, шаг решает сам по базе
    синхронизации (см. докстринг модуля). `force` — загружать всё, не
    сверяясь ни с базой, ни с ревизией на Polygon (незакоммиченные правки
    на Polygon останавливают push и с `force`).

    После каждого шага с `commits_changes=True`, который что-то загрузил, —
    `problem.commitChanges` и запись базы (см. `_commit_and_record`). Ошибка
    коммита — такая же ошибка шага: шаг получает статус "error", пайплайн
    останавливается. Загруженное, но не закоммиченное остаётся в рабочей
    копии Polygon — следующий push увидит незакоммиченные правки и
    остановится (нужен `--force`).

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
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return PolygonPipelineResult(problem_id=problem_id, spec_error=str(exc))

    client = client or PolygonClient()
    result = PolygonPipelineResult(problem_id=problem_id)
    steps_to_run = POLYGON_STEP_ORDER if only_step is None else [only_step]

    def record(outcome: PolygonStepOutcome) -> None:
        result.outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    baseline_checked = False
    synced: dict[str, str] | None = None

    for step_name in steps_to_run:
        step = _POLYGON_STEPS[step_name]
        logger.info("[%s] step started", step_name)
        try:
            if step.commits_changes and not baseline_checked:
                synced = _sync_baseline(
                    problem_id, force=force, problems_dir=problems_dir, client=client
                )
                baseline_checked = True
            step_result = run_step(
                step, problem_id, spec, problems_dir=problems_dir, client=client, synced=synced
            )
            detail = step_result.message
            if step.commits_changes and step_result.uploaded:
                detail += "; " + _commit_and_record(
                    problem_id,
                    step_name,
                    step_result.uploaded,
                    problems_dir=problems_dir,
                    client=client,
                )
        except (PolygonApiError, PolygonStepError) as exc:
            logger.error("[%s] %s", step_name, exc)
            record(
                PolygonStepOutcome(
                    step_name,
                    OUTCOME_ERROR,
                    str(exc),
                    full_detail=isinstance(exc, PolygonStepError),
                )
            )
            return result
        except Exception as exc:  # noqa: BLE001 — traceback в лог, а не в консоль
            logger.exception("[%s] unexpected error", step_name)
            record(
                PolygonStepOutcome(step_name, OUTCOME_ERROR, f"{type(exc).__name__}: {exc}")
            )
            return result
        record(PolygonStepOutcome(step_name, OUTCOME_OK, detail))

    return result


def _sync_baseline(
    problem_id: str, *, force: bool, problems_dir: Path, client: PolygonClient
) -> dict[str, str] | None:
    """База синхронизации для шагов этого прогона (см. докстринг модуля);
    `None` — загружать всё.

    Задача ещё не привязана — `None` без сети: шаг сам упадёт с указанием
    запустить `create_problem`. Polygon меняли в обход оркестратора —
    `PolygonStepError`.

    Незакоммиченные правки в рабочей копии на Polygon останавливают push
    всегда, в том числе с `force`: коммит после шага коммитит рабочую копию
    целиком и унёс бы чужие правки в ревизию оркестратора (а правки в
    перезаписанных файлах потерялись бы без следа в истории).
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        return None

    address = problem_address(problems_dir, problem_id)
    current = fetch_revision(client, state.polygon_id)
    if current.modified:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={state.polygon_id} has uncommitted changes "
            "in its working copy — commit or discard them on Polygon first (push refuses "
            "to commit them, even with --force), then run "
            f"`orchestrator {address} polygon pull [--overwrite]`"
        )
    if force:
        logger.info("--force: uploading everything, the sync baseline is ignored")
        return None
    if state.revision is None:
        raise PolygonStepError(
            f"'{problem_id}': the Polygon revision of the last sync is unknown (the problem "
            "was linked to an existing Polygon problem or before revisions were recorded) — "
            f"run `orchestrator {address} polygon pull [--overwrite]` first, or overwrite "
            f"Polygon with `orchestrator {address} polygon push --force`"
        )
    if current.revision != state.revision:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={state.polygon_id} changed since the last "
            f"sync (revision {current.revision} on Polygon, {state.revision} recorded) — "
            f"run `orchestrator {address} polygon pull [--overwrite]`, or overwrite "
            f"Polygon with `orchestrator {address} polygon push --force`"
        )
    logger.info("Polygon revision %s matches the last sync", current.revision)
    return dict(state.synced)


def _commit_and_record(
    problem_id: str,
    step_name: str,
    uploaded: dict[str, str],
    *,
    problems_dir: Path,
    client: PolygonClient,
) -> str:
    """Коммитит рабочую копию задачи на Polygon после шага `step_name` и
    записывает в базу синхронизации загруженное шагом и новую ревизию.

    `minorChanges=true` — без email-уведомлений: коммит идёт после каждого
    шага, иначе один прогон рассылал бы письмо на каждый шаг.

    Новая ревизия перечитывается с Polygon (`problems.list`), а не
    вычисляется как "+1" — так база не зависит от того, как Polygon
    нумерует ревизии.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': nothing to commit after {step_name} — the problem is not "
            "linked to Polygon (polygon_state.json is missing or corrupted)"
        )
    client.call(
        "problem.commitChanges",
        {
            "problemId": str(state.polygon_id),
            "minorChanges": "true",
            "message": _COMMIT_MESSAGE_PREFIX + step_name,
        },
    )
    revision = fetch_revision(client, state.polygon_id).revision
    update_sync(problem_id, synced=uploaded, revision=revision, problems_dir=problems_dir)
    return f"committed on Polygon id={state.polygon_id} (revision {revision})"


def compute_polygon_step_statuses(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR
) -> list[PolygonStepOutcome]:
    """Состояние polygon-шагов для `orchestrator <задача> polygon status`, без сети:
    по `compute_status` каждого шага, в порядке `POLYGON_STEP_ORDER`.

    Состояние — только по локальным данным (база синхронизации в
    `polygon_state.json` и входные файлы шага); ручные правки на стороне
    Polygon отсюда не видны.
    """
    outcomes = []
    for step_name in POLYGON_STEP_ORDER:
        status, detail = _POLYGON_STEPS[step_name].compute_status(problem_id, Path(problems_dir))
        outcomes.append(PolygonStepOutcome(step_name, status, detail))
    return outcomes
