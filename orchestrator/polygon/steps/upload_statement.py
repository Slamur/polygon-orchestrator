"""Polygon-шаг 6 — загрузка условия из `problems/<problem_id>/statement/`
(результат генеративного шага `statement_draft`): legend/input/output/notes
через `problem.saveStatement` и тестовые примеры как первые тесты
(`problem.saveTest`, `testIndex` 1..N, `testUseInStatements=true`).

В Polygon уходит только input примеров — output из `sample_examples` в этом
шаге не читается и не используется (docs/SPEC_FORMAT.md: output "никуда
дальше... не транслируется"). Answer для этих тестов Polygon посчитает сам
по главному решению, когда оно будет загружено отдельным (ещё не
написанным) шагом — это осознанный порядок, не ошибка.

`testIndex` примеров всегда 1..N без сдвига: templates/tutorials/statement.md
требует, чтобы тестовые примеры были первыми тестами.

Параметры `problem.saveTest` — по docs/POLYGON_API.md. Флаг "использовать
в условии" называется `testUseInStatements` (`useInStatements` — имя поля в
возвращаемом объекте Test, не параметра): неизвестный параметр Polygon
молча игнорирует, и тест загружается без этого флага.

Тестовые группы (`generation.script_style: "groups"`) этим шагом НЕ
поддерживаются: примеры загружаются без `testGroup`, т.е. в группу Polygon
по умолчанию. Для задачи с группами это может быть не то, что нужно, —
осознанное ограничение MVP, группу примеров придётся выставить руками.

Если число примеров уменьшилось с прошлой загрузки, лишние тесты с
индексами > N на Polygon остаются — шаг их не удаляет.

Название задачи берётся из `statement/name.txt`, а не из спека: `title.ru`
спека — только начальное значение, которым шаг заполняет `name.txt`, если
его ещё нет. Иначе название, поменянное на Polygon и выгруженное `polygon
pull` (он пишет его в тот же `name.txt`), следующий push перезаписал бы
значением из спека.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import re
from pathlib import Path

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
from orchestrator.polygon.sync import (
    STATEMENT_NAME_KEY,
    digest,
    example_key,
    statement_file_key,
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

NAME_FILENAME = "name.txt"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает условие (если какой-то из tex-файлов или название
    отличаются от базы синхронизации — `problem.saveStatement` принимает
    их только вместе), затем изменившиеся примеры по порядку `testIndex`.

    Обе зависимости (`create_problem`, `statement_draft`) проверяются до
    первого сетевого вызова.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    statement_dir = _statement_dir(ctx.problem_id, ctx.problems_dir)
    tex_files = _read_statement_dir(statement_dir)
    examples = _read_examples(statement_dir)
    name_path = statement_dir / NAME_FILENAME
    if not name_path.exists():
        name_path.write_text(ctx.spec.title.ru + "\n", encoding="utf-8")
    name = read_name(name_path)

    statement_items = {**_tex_items(tex_files), STATEMENT_NAME_KEY: digest(name)}
    example_items = _example_items(examples)
    uploaded: dict[str, str] = {}
    done: list[str] = []

    if changed_items(ctx, statement_items):
        client.call(
            "problem.saveStatement",
            {
                "problemId": str(polygon_id),
                "lang": _LANG,
                "name": name,
                **{
                    _TEX_PARAM_NAMES[filename]: content.decode("utf-8")
                    for filename, content in tex_files.items()
                },
            },
        )
        uploaded.update(statement_items)
        done.append(f"saved statement (name={name!r})")

    changed_examples = changed_items(ctx, example_items)
    sent_examples = 0
    for test_index, content in examples:
        if example_key(test_index) not in changed_examples:
            continue
        client.call(
            "problem.saveTest",
            {
                "problemId": str(polygon_id),
                "testset": _TESTSET,
                "testIndex": str(test_index),
                "testInput": content.decode("utf-8"),
                "testUseInStatements": "true",
            },
        )
        sent_examples += 1
    if sent_examples:
        uploaded.update(changed_examples)
        done.append(f"{sent_examples} of {len(examples)} sample test(s)")

    if not uploaded:
        return StepResult(NO_CHANGES_MESSAGE)
    return StepResult(f"{' and '.join(done)} on Polygon id={polygon_id}", uploaded)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Пока `name.txt` нет (его
    создаст push из `title.ru` спека), название не сравнивается."""

    def read_items() -> dict[str, str]:
        statement_dir = _statement_dir(problem_id, problems_dir)
        items = {
            **_tex_items(_read_statement_dir(statement_dir)),
            **_example_items(_read_examples(statement_dir)),
        }
        name_path = statement_dir / NAME_FILENAME
        if name_path.exists():
            items[STATEMENT_NAME_KEY] = digest(read_name(name_path))
        return items

    return sync_status(problem_id, problems_dir, key_prefix="statement/", read_items=read_items)


def read_name(path: Path) -> str:
    """Название задачи из `name.txt` без окружающих пробелов и переводов
    строк — их добавляют редакторы, в название они не входят."""
    name = path.read_text(encoding="utf-8").strip()
    if not name:
        raise PolygonStepError(f"{path} is empty — write the problem name into it")
    return name


def _tex_items(tex_files: dict[str, bytes]) -> dict[str, str]:
    return {statement_file_key(name): digest(content) for name, content in tex_files.items()}


def _example_items(examples: list[tuple[int, bytes]]) -> dict[str, str]:
    return {example_key(index): digest(content) for index, content in examples}


def _statement_dir(problem_id: str, problems_dir: Path) -> Path:
    return Path(problems_dir) / problem_id / "statement"


def _read_statement_dir(statement_dir: Path) -> dict[str, bytes]:
    """Содержимое 4 фиксированных tex-файлов (имя файла -> содержимое).
    Если какого-то нет — `PolygonStepError` с его именем, а не пустая
    строка вместо содержимого."""
    missing = [name for name in _TEX_PARAM_NAMES if not (statement_dir / name).exists()]
    if missing:
        raise PolygonStepError(
            f"files missing in {statement_dir}: {missing} — first "
            "run the generative step statement_draft"
        )
    return {filename: (statement_dir / filename).read_bytes() for filename in _TEX_PARAM_NAMES}


def _read_examples(statement_dir: Path) -> list[tuple[int, bytes]]:
    """[(testIndex, input), ...] по возрастанию `testIndex`.

    Нумерация `example_<N>.txt` должна быть непрерывной 1..N. Набор файлов
    уже проверяет `statement_draft` перед записью, но этот шаг запускается
    независимо от него, поэтому проверка повторяется здесь.
    """
    examples_dir = statement_dir / "examples"
    if not examples_dir.is_dir():
        raise PolygonStepError(
            f"directory {examples_dir} not found — first run "
            "the generative step statement_draft"
        )

    found: dict[int, bytes] = {}
    for path in examples_dir.iterdir():
        match = _EXAMPLE_PATTERN.match(path.name)
        if match:
            found[int(match.group(1))] = path.read_bytes()

    if not found:
        raise PolygonStepError(f"no example_<N>.txt files in {examples_dir}")

    if set(found) != set(range(1, len(found) + 1)):
        raise PolygonStepError(
            f"example_<N>.txt numbering in {examples_dir} is not contiguous "
            f"1..{len(found)}: found indices {sorted(found)}"
        )

    return sorted(found.items())


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
