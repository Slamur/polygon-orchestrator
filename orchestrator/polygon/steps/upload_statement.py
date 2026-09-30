"""Polygon-шаг 6 — загрузка условия из `outputs/<problem_id>/statement/`
(результат генеративного шага `statement_draft`): legend/input/output/notes
через `problem.saveStatement` и тестовые примеры как первые тесты
(`problem.saveTest`, `testIndex` 1..N, `useInStatements=true`).

В Polygon уходит только input примеров — output из `sample_examples` в этом
шаге не читается и не используется (docs/SPEC_FORMAT.md: output "никуда
дальше... не транслируется"). Answer для этих тестов Polygon посчитает сам
по главному решению, когда оно будет загружено отдельным (ещё не
написанным) шагом — это осознанный порядок, не ошибка.

`testIndex` примеров всегда 1..N без сдвига: templates/tutorials/requirements.md
требует, чтобы тестовые примеры были первыми тестами.

Параметры `problem.saveTest` (`testset`, `testIndex`, `testInput`,
`useInStatements`) подтверждены только косвенно, не текстом документации
API. Если первый реальный вызов вернёт FAILED с жалобой на параметр — это
первое, что проверить (возможные варианты: `testUseInStatements`,
обязательный `testAnswer`).

Тестовые группы (`generation.script_style: "groups"`) этим шагом НЕ
поддерживаются: примеры загружаются без `testGroup`, т.е. в группу Polygon
по умолчанию. Для задачи с группами это может быть не то, что нужно, —
осознанное ограничение MVP, группу примеров придётся выставить руками.

Если число примеров уменьшилось с прошлой загрузки, лишние тесты с
индексами > N на Polygon остаются — шаг их не удаляет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
import hashlib
import re
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
)

STEP_NAME = "upload_statement"

# Спек пока поддерживает только русскоязычное условие (title.ru) — язык
# захардкожен, а не берётся из спека.
_LANG = "russian"
_TESTSET = "tests"

_TEX_PARAM_NAMES = {
    "legend.tex": "legend",
    "input_format.tex": "input",
    "output_format.tex": "output",
    "notes.tex": "notes",
}

_EXAMPLE_PATTERN = re.compile(r"^example_(\d+)\.txt$")


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveStatement` и
    `problem.saveTest` перезаписывают условие и тесты, а ручные правки в UI
    Polygon локально не видны — пропускать по локальной записи нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Загружает условие, затем примеры по порядку `testIndex`; записывает
    sha256 загруженного содержимого в `polygon_state.json` — для `status`.

    Обе зависимости (`create_problem`, `statement_draft`) проверяются до
    первого сетевого вызова. Запись делается только после успеха всех
    вызовов: если упал какой-то `saveTest`, шаг остаётся "not run"/"stale"
    и повторится целиком при следующем запуске.
    """
    polygon_id = _require_polygon_id(ctx.problem_id, ctx.outputs_dir)
    statement_dir = _statement_dir(ctx.problem_id, ctx.outputs_dir)
    tex_params = _read_statement_dir(statement_dir)
    examples = _read_examples(statement_dir)

    client.call(
        "problem.saveStatement",
        {
            "problemId": str(polygon_id),
            "lang": _LANG,
            "name": ctx.spec.title.ru,
            **tex_params,
        },
    )
    for test_index, content in examples:
        client.call(
            "problem.saveTest",
            {
                "problemId": str(polygon_id),
                "testset": _TESTSET,
                "testIndex": str(test_index),
                "testInput": content,
                "useInStatements": "true",
            },
        )

    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "sha256": _content_hash(tex_params, examples),
            "examples": len(examples),
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    return (
        f"saved statement (name={ctx.spec.title.ru!r}) and {len(examples)} "
        f"sample test(s) on Polygon id={polygon_id}"
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если последнее загруженное содержимое `statement/` совпадает
    по sha256 с текущим; "stale", если файлы с тех пор изменились (или
    больше не читаются); "not run", если шаг ещё ни разу не выполнялся
    успешно.

    `name` (`title.ru` из спека) в хеш не входит — `compute_status` не
    получает спек, так что правка заголовка отсюда не видна.
    """
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        return STATUS_NOT_RUN, ""

    statement_dir = _statement_dir(problem_id, outputs_dir)
    try:
        tex_params = _read_statement_dir(statement_dir)
        examples = _read_examples(statement_dir)
    except PolygonStepError as exc:
        return STATUS_STALE, f"текущее условие не читается: {exc}"
    current = _content_hash(tex_params, examples)
    if current != record["sha256"]:
        return STATUS_STALE, "statement/ изменился после последней загрузки"
    return STATUS_DONE, f"{len(examples)} sample test(s), sha256={current[:12]}"


def _require_polygon_id(problem_id: str, outputs_dir: Path) -> int:
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': задача ещё не создана на Polygon — сначала "
            f"выполните шаг create_problem (orchestrator polygon {problem_id} "
            "run --step create_problem)"
        )
    return state.polygon_id


def _statement_dir(problem_id: str, outputs_dir: Path) -> Path:
    return Path(outputs_dir) / problem_id / "statement"


def _read_statement_dir(statement_dir: Path) -> dict[str, str]:
    """Параметры `problem.saveStatement` из 4 фиксированных tex-файлов.
    Если какого-то нет — `PolygonStepError` с его именем, а не пустая
    строка вместо содержимого."""
    missing = [name for name in _TEX_PARAM_NAMES if not (statement_dir / name).exists()]
    if missing:
        raise PolygonStepError(
            f"в {statement_dir} не хватает файлов: {missing} — сначала "
            "выполните генеративный шаг statement_draft"
        )
    return {
        param_name: (statement_dir / filename).read_text(encoding="utf-8")
        for filename, param_name in _TEX_PARAM_NAMES.items()
    }


def _read_examples(statement_dir: Path) -> list[tuple[int, str]]:
    """[(testIndex, input), ...] по возрастанию `testIndex`.

    Нумерация `example_<N>.txt` должна быть непрерывной 1..N. Набор файлов
    уже проверяет `statement_draft` перед записью, но этот шаг запускается
    независимо от него, поэтому проверка повторяется здесь.
    """
    examples_dir = statement_dir / "examples"
    if not examples_dir.is_dir():
        raise PolygonStepError(
            f"не найден каталог {examples_dir} — сначала выполните "
            "генеративный шаг statement_draft"
        )

    found: dict[int, str] = {}
    for path in examples_dir.iterdir():
        match = _EXAMPLE_PATTERN.match(path.name)
        if match:
            found[int(match.group(1))] = path.read_text(encoding="utf-8")

    if not found:
        raise PolygonStepError(f"в {examples_dir} нет файлов example_<N>.txt")

    if set(found) != set(range(1, len(found) + 1)):
        raise PolygonStepError(
            f"нумерация example_<N>.txt в {examples_dir} не непрерывна "
            f"1..{len(found)}: найдены индексы {sorted(found)}"
        )

    return sorted(found.items())


def _content_hash(tex_params: dict[str, str], examples: list[tuple[int, str]]) -> str:
    digest = hashlib.sha256()
    for part in [*tex_params.values(), *(content for _, content in examples)]:
        data = part.encode("utf-8")
        # длина перед содержимым — чтобы перенос текста между файлами менял хеш
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
