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

Polygon при сохранении скрипта выкидывает из него пустые строки, поэтому
хеш считается от `normalize_script` (без пустых строк и пробелов в концах
строк) — иначе `polygon pull` видел бы расхождение с локальным файлом и
сбрасывал базу, а следующий push загружал бы скрипт заново. Загружается
при этом исходный файл как есть.

Перед загрузкой скрипт раскрывается локально и проверяется на одинаковые
строки и строки с кавычками или лишним `>` в параметрах генератора
(`orchestrator/checks/test_script_check.py`, то же делает `local check`):
такой скрипт Polygon не примет или не сгенерирует по нему тесты, поэтому
шаг падает до обращения к сети, с отчётом, какие строки скрипта их
напечатали. Если
проверку выполнить нельзя (нет `java`, не скачался FreeMarker) или
FreeMarker не раскрыл скрипт, — только предупреждение в лог: локальное
раскрытие может разойтись с Polygon, и загрузку оно не блокирует.
`ORCHESTRATOR_SKIP_SCRIPT_CHECK=1` выключает проверку совсем.

Сохранив скрипт, Polygon тут же генерирует по нему тесты и отвечает только
после этого — на большом наборе тестов дольше обычного таймаута клиента.
Поэтому `problem.saveScript` ждёт ответа `_SAVE_SCRIPT_TIMEOUT_SECONDS`, а
если не дождался — скрипт перечитывается с Polygon (`problem.script`): тот
же скрипт, что загружали (после `normalize_script`), — загрузка считается
прошедшей, и pipeline коммитит её как обычно (иначе в рабочей копии Polygon
остались бы незакоммиченные правки, и следующий push остановился бы на
них); другой — `PolygonStepError`.

По смыслу шаг идёт после `upload_generators` — скрипт ссылается на
генераторы по имени, — хотя Polygon при сохранении скрипта этого не
проверяет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from orchestrator.checks.test_script_check import (
    SKIP_CHECK_ENV,
    ScriptCheckUnavailable,
    ScriptRenderError,
    check_script,
)
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
from orchestrator.polygon.sync import SCRIPT_KEY, digest
from orchestrator.problem_paths import problem_address
from orchestrator.steps.generators_and_script import (
    primary_artifact_path as script_artifact_path,
)

STEP_NAME = "upload_test_script"

logger = logging.getLogger(__name__)

_TESTSET = "tests"

# Сколько ждать ответа `problem.saveScript`: Polygon отвечает, только
# сгенерировав тесты (см. докстринг модуля).
_SAVE_SCRIPT_TIMEOUT_SECONDS = 600

# Возможные имена файла скрипта — для `_compute_status`, где спека нет.
SCRIPT_FILENAMES = ("test_script", "test_script_groups")


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает test-script, если он отличается от базы синхронизации.

    Все три проверки (`script_style != "groups"`, `create_problem`,
    наличие файла скрипта) — до первого сетевого вызова.
    """
    if ctx.spec.generation.script_style == "groups":
        raise PolygonStepError(
            f"'{ctx.problem_id}': script_style='groups' is not supported by this "
            "step (requires a separate problem.enableGroups, not implemented) — "
            "see the upload_test_script.py docstring"
        )

    polygon_id = require_polygon_state(ctx).polygon_id
    script_path = script_artifact_path(ctx.problem_id, ctx.spec, problems_dir=ctx.problems_dir)
    content = _read_script(ctx.problem_id, ctx.problems_dir, script_path)
    items = {SCRIPT_KEY: digest(normalize_script(content))}
    if not changed_items(ctx, items):
        return StepResult(NO_CHANGES_MESSAGE)

    _check_script_lines(ctx.problem_id, script_path.name, content)
    _save_script(ctx.problem_id, polygon_id, content, client)
    return StepResult(
        f"uploaded test script ({script_path.name}) to Polygon id={polygon_id}", items
    )


def _save_script(
    problem_id: str, polygon_id: int, content: bytes, client: PolygonClient
) -> None:
    """`problem.saveScript`; если ответа не дождались — проверка, что скрипт
    на Polygon всё же сохранён (см. докстринг модуля)."""
    params = {"problemId": str(polygon_id), "testset": _TESTSET}
    try:
        client.call(
            "problem.saveScript",
            {**params, "source": content.decode("utf-8")},
            timeout=_SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
        return
    except requests.ReadTimeout:
        logger.warning(
            "'%s': no response to problem.saveScript in %ss (Polygon is probably still "
            "generating tests) — checking the script saved on Polygon",
            problem_id,
            _SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
    saved = client.call_raw("problem.script", params)
    if normalize_script(saved) != normalize_script(content):
        raise PolygonStepError(
            f"'{problem_id}': no response to problem.saveScript in "
            f"{_SAVE_SCRIPT_TIMEOUT_SECONDS}s, and the script on Polygon id={polygon_id} "
            "differs from the uploaded one — check the problem on Polygon"
        )
    logger.info("'%s': the script on Polygon matches the uploaded one", problem_id)


def _check_script_lines(problem_id: str, script_name: str, content: bytes) -> None:
    """`PolygonStepError` с отчётом, если в раскрытом скрипте есть одинаковые
    строки или строки, которые Polygon не примет (см. докстринг модуля)."""
    if os.environ.get(SKIP_CHECK_ENV):
        return
    try:
        result = check_script(content.decode("utf-8"), script_name)
    except (ScriptCheckUnavailable, ScriptRenderError) as exc:
        logger.warning(
            "'%s': line check of %s skipped — %s", problem_id, script_name, exc
        )
        return
    if result.failed:
        raise PolygonStepError(
            f"'{problem_id}': {result.report}\n(set {SKIP_CHECK_ENV}=1 to upload anyway)"
        )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Спек здесь недоступен,
    поэтому сравнивается первый существующий из `SCRIPT_FILENAMES`."""

    def read_items() -> dict[str, str]:
        problem_dir = Path(problems_dir) / problem_id
        existing = [name for name in SCRIPT_FILENAMES if (problem_dir / name).exists()]
        script_path = problem_dir / (existing[0] if existing else SCRIPT_FILENAMES[0])
        content = _read_script(problem_id, problems_dir, script_path)
        return {SCRIPT_KEY: digest(normalize_script(content))}

    return sync_status(problem_id, problems_dir, key_prefix=SCRIPT_KEY, read_items=read_items)


def normalize_script(content: bytes) -> bytes:
    """Скрипт в том виде, в каком его сравнивают `push` и `pull`: без пустых
    строк (их выкидывает Polygon), без пробелов в концах строк и с LF вместо
    CRLF."""
    lines = content.decode("utf-8").replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines if line.strip()).encode("utf-8")


def _read_script(problem_id: str, problems_dir: Path, script_path: Path) -> bytes:
    """Содержимое test-script. Путь передаётся снаружи: в `_execute` он
    зависит от `script_style` спека (`primary_artifact_path`), а в
    `_compute_status` спека нет."""
    if not script_path.exists():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': {script_path} not found — first run "
            f"the generative step generators_and_script (`orchestrator {address} llm generate "
            f"--step generators_and_script`)"
        )
    return script_path.read_bytes()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
