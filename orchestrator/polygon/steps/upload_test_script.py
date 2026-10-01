"""Polygon-шаг 8 — загрузка FreeMarker test-script'а (результат генеративного
шага `generators_and_script`) в Polygon (`problem.saveScript`,
`testset=tests`).

Путь к файлу скрипта берётся из
`orchestrator.steps.generators_and_script.primary_artifact_path` — единый
источник истины для имени (`test_script`/`test_script_groups` по
`generation.script_style`), здесь ветвление flat/groups не дублируется.

Ограничение MVP: `generation.script_style == "groups"` сознательно
отклоняется `PolygonStepError` до любой другой логики — groups-режим в
Polygon дополнительно требует `problem.enableGroups` (не спроектирован), а
groups-шаблонов в `templates/` сейчас нет. Явная ошибка лучше тихого
неверного поведения.

По смыслу шаг идёт после `upload_generators` — скрипт ссылается на
генераторы по имени, — хотя Polygon при сохранении скрипта этого не
проверяет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state, record_polygon_step
from orchestrator.polygon.steps.base import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    STATUS_STALE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    require_polygon_state,
)
from orchestrator.steps.generators_and_script import (
    primary_artifact_path as script_artifact_path,
)

STEP_NAME = "upload_test_script"

_TESTSET = "tests"


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveScript` перезаписывает
    скрипт, а ручные правки в UI Polygon локально не видны — пропускать
    загрузку по локальной записи нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Загружает test-script и записывает имя файла и его sha256 в
    `polygon_state.json` — для `status`.

    Все три проверки (`script_style != "groups"`, `create_problem`,
    наличие файла скрипта) — до первого сетевого вызова.
    """
    if ctx.spec.generation.script_style == "groups":
        raise PolygonStepError(
            f"'{ctx.problem_id}': script_style='groups' не поддерживается этим "
            "шагом (нужен отдельный problem.enableGroups, не реализован) — "
            "см. докстринг upload_test_script.py"
        )

    polygon_id = require_polygon_state(ctx).polygon_id
    script_path = script_artifact_path(ctx.problem_id, ctx.spec, outputs_dir=ctx.outputs_dir)
    content = _read_script(ctx.problem_id, script_path)

    client.call(
        "problem.saveScript",
        {
            "problemId": str(polygon_id),
            "testset": _TESTSET,
            "source": content.decode("utf-8"),
        },
    )

    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "file": script_path.name,
            "sha256": _sha256(content),
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    return f"uploaded test script ({script_path.name}) to Polygon id={polygon_id}"


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если sha256 последнего загруженного скрипта совпадает с
    текущим содержимым того же файла; "stale", если файл изменился или
    пропал; "not run", если шаг ещё ни разу не выполнялся успешно.

    Спек здесь недоступен, поэтому имя файла берётся из записи в
    `polygon_state.json`, а не из `primary_artifact_path`."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not (
        isinstance(record, dict)
        and isinstance(record.get("file"), str)
        and isinstance(record.get("sha256"), str)
    ):
        return STATUS_NOT_RUN, ""

    script_path = Path(outputs_dir) / problem_id / record["file"]
    try:
        content = _read_script(problem_id, script_path)
    except PolygonStepError as exc:
        return STATUS_STALE, f"текущий test-script не читается: {exc}"
    current = _sha256(content)
    if current != record["sha256"]:
        return STATUS_STALE, f"{record['file']} изменился после последней загрузки"
    return STATUS_DONE, f"{record['file']} sha256={current[:12]}"


def _read_script(problem_id: str, script_path: Path) -> bytes:
    """Содержимое test-script. Путь передаётся снаружи: в `_execute` он
    зависит от `script_style` спека (`primary_artifact_path`), а в
    `_compute_status` спека нет и имя берётся из `polygon_state.json`."""
    if not script_path.exists():
        raise PolygonStepError(
            f"'{problem_id}': не найден {script_path} — сначала выполните "
            f"генеративный шаг generators_and_script (orchestrator run "
            f"{problem_id} --step generators_and_script)"
        )
    return script_path.read_bytes()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
