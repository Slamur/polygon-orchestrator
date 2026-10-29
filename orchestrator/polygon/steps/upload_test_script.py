"""Polygon-шаг — загрузка FreeMarker test-script'а (результат генеративного
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

Сохранив скрипт, Polygon тут же генерирует по нему тесты, а коммитить
рабочую копию до конца генерации бессмысленно. Поэтому шаг идёт в push
последним, `problem.saveScript` ждёт ответа `_SAVE_SCRIPT_TIMEOUT_SECONDS`
(на большом наборе тестов — дольше обычного таймаута клиента; повторять
запрос нельзя — генерация начнётся заново), а потом шаг опрашивает
`problem.tests` с растущей паузой, пока `scriptLine` сгенерированных тестов
по порядку не совпадут с командами раскрытого скрипта (строка скрипта без
`> $`), — не дольше `_GENERATION_WAIT_SECONDS`. Не совпали — `PolygonStepError`
без коммита: загруженный скрипт остаётся в рабочей копии Polygon, и следующий
push остановится на незакоммиченных правках. Если скрипт локально не раскрыт
(проверка выключена или недоступна), признак конца генерации — только ответ
на `problem.saveScript`: дождались — шаг проходит, нет — `PolygonStepError`.

По смыслу шаг идёт после `upload_generators` — скрипт ссылается на
генераторы по имени, — хотя Polygon при сохранении скрипта этого не
проверяет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import itertools
import logging
import os
import time
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

# Ожидание конца генерации тестов: опрос `problem.tests` с паузой, растущей
# вдвое от первой до максимальной, пока не выйдет общий лимит.
_GENERATION_POLL_FIRST_DELAY_SECONDS = 5
_GENERATION_POLL_MAX_DELAY_SECONDS = 120
_GENERATION_WAIT_SECONDS = 1800

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

    expected = _check_script_lines(ctx.problem_id, script_path.name, content)
    timed_out = _save_script(ctx.problem_id, polygon_id, content, client)
    waited = _wait_for_tests(ctx.problem_id, polygon_id, expected, timed_out, client)
    return StepResult(
        f"uploaded test script ({script_path.name}) to Polygon id={polygon_id}{waited}", items
    )


def _save_script(
    problem_id: str, polygon_id: int, content: bytes, client: PolygonClient
) -> bool:
    """`problem.saveScript`; `True` — ответа не дождались (тесты, скорее
    всего, ещё генерируются, см. докстринг модуля)."""
    try:
        client.call(
            "problem.saveScript",
            {"problemId": str(polygon_id), "testset": _TESTSET, "source": content.decode("utf-8")},
            timeout=_SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
    except requests.ReadTimeout:
        logger.warning(
            "'%s': no response to problem.saveScript in %ss — Polygon is probably still "
            "generating tests",
            problem_id,
            _SAVE_SCRIPT_TIMEOUT_SECONDS,
        )
        return True
    return False


def _wait_for_tests(
    problem_id: str,
    polygon_id: int,
    expected: list[str] | None,
    timed_out: bool,
    client: PolygonClient,
) -> str:
    """Ждёт, пока сгенерированные тесты на Polygon не совпадут с раскрытым
    скриптом (см. докстринг модуля); возвращает приписку к сообщению шага.

    `expected` — `None`, если скрипт локально не раскрыт: ответ на
    `problem.saveScript` тогда — единственный признак конца генерации, и без
    него шаг падает, не коммитя недогенерированные тесты.
    """
    if expected is None:
        if timed_out:
            raise PolygonStepError(
                f"'{problem_id}': no response to problem.saveScript in "
                f"{_SAVE_SCRIPT_TIMEOUT_SECONDS}s and the script was not expanded locally, so "
                "the end of test generation cannot be checked — wait for it on Polygon, commit "
                "there, then run `polygon pull`"
            )
        return ""

    started = time.monotonic()
    for attempt in itertools.count():
        generated = _generated_script_lines(polygon_id, client)
        if generated == expected:
            break
        elapsed = time.monotonic() - started
        if elapsed >= _GENERATION_WAIT_SECONDS:
            raise PolygonStepError(
                f"'{problem_id}': tests on Polygon id={polygon_id} still do not match the script "
                f"after {int(elapsed)}s ({_progress(generated, expected)}) — check test "
                "generation on Polygon, commit there, then run `polygon pull`"
            )
        delay = min(
            _GENERATION_POLL_FIRST_DELAY_SECONDS * 2**attempt, _GENERATION_POLL_MAX_DELAY_SECONDS
        )
        logger.info(
            "'%s': waiting for test generation (%s), next check in %ss",
            problem_id,
            _progress(generated, expected),
            delay,
        )
        time.sleep(delay)
    if not attempt:
        return ""
    return f"; waited {int(time.monotonic() - started)}s for test generation"


def _generated_script_lines(polygon_id: int, client: PolygonClient) -> list[str] | None:
    """`scriptLine` сгенерированных тестов по порядку; `None`, если Polygon не
    ответил вовремя (занят генерацией)."""
    try:
        tests = client.call(
            "problem.tests",
            {"problemId": str(polygon_id), "testset": _TESTSET, "noInputs": "true"},
        )
    except requests.ReadTimeout:
        return None
    return [_normalize_command(t.get("scriptLine", "")) for t in tests if not t.get("manual")]


def _progress(generated: list[str] | None, expected: list[str]) -> str:
    if generated is None:
        return "Polygon did not respond"
    matched = sum(1 for g, e in zip(generated, expected) if g == e)
    return f"{matched}/{len(expected)} tests match the script"


def _normalize_command(line: str) -> str:
    """Команда генератора, как её показывает `scriptLine` теста Polygon: без
    перенаправления `> $` и с одиночными пробелами."""
    return " ".join(line.split(">", 1)[0].split())


def _check_script_lines(problem_id: str, script_name: str, content: bytes) -> list[str] | None:
    """`PolygonStepError` с отчётом, если в раскрытом скрипте есть одинаковые
    строки или строки, которые Polygon не примет (см. докстринг модуля).

    Возвращает команды раскрытого скрипта (`_normalize_command`) — по ним
    `_wait_for_tests` ждёт конца генерации; `None`, если скрипт не раскрыт.
    """
    if os.environ.get(SKIP_CHECK_ENV):
        return None
    try:
        result = check_script(content.decode("utf-8"), script_name)
    except (ScriptCheckUnavailable, ScriptRenderError) as exc:
        logger.warning(
            "'%s': line check of %s skipped — %s", problem_id, script_name, exc
        )
        return None
    if result.failed:
        raise PolygonStepError(
            f"'{problem_id}': {result.report}\n(set {SKIP_CHECK_ENV}=1 to upload anyway)"
        )
    return [_normalize_command(line.text) for line in result.lines]


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
