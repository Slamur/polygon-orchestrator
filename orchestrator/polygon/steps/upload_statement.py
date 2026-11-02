"""Polygon-шаг 6 — загрузка условия из `problems/<problem_id>/statement/<код
языка>/` (на основном языке — результат генеративного шага `statement_draft`):
legend/input/output/notes через `problem.saveStatement` и тестовые примеры
из `statement/examples/` как первые тесты
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

Интерактивная задача (в спеке есть секция `interactor`): протокол
взаимодействия `statement/<код>/interaction.tex` уходит в `problem.saveStatement`
параметром `interaction` (без файла — ошибка).

Разбор `statement/<код>/tutorial.tex` (необязательный, пишет автор, `polygon pull`
или шаг `tutorial_draft`) уходит туда же параметром `tutorial`. Файла нет —
параметр не передаётся (Polygon оставляет разбор как есть), но если в базе
синхронизации разбор был, а файл удалили, разбор очищается пустой строкой.

Input/output for statements примера (`example_<N>.input_for_statement.txt`,
`example_<N>.output_for_statement.txt` — каждый сам по себе, у обычной
задачи их задаёт автор, у интерактивной это диалог решения с интерактором)
уходят в `problem.saveTest` параметрами
`testInputForStatements`/`testOutputForStatements`, флаг
`example_<N>.verify_for_statement.txt` (файл есть и содержит `true`) —
`verifyInputOutputForStatements=true`. Файла нет — параметр не
передаётся (Polygon оставляет поле как есть), но если в базе синхронизации
поле было, а файл удалили, поле очищается: пустая строка, у флага —
`false`.

Название задачи берётся из `statement/<код>/name.txt`, а не из спека:
название спека на этом языке (`title.ru` у `ru`) — только начальное
значение, которым шаг заполняет `name.txt`, если его ещё нет (нет и
названия в спеке — ошибка). Иначе название, поменянное на Polygon и
выгруженное `polygon pull` (он пишет его в тот же `name.txt`), следующий
push перезаписал бы значением из спека.

Языки (`orchestrator/statement_languages.py`): каждый каталог
`statement/<код>/` уходит своим `problem.saveStatement` с `lang` — именем
языка в Polygon (`ru` -> `russian`). Каталог основного языка (`language`
спека) обязателен, остальные пишет автор или `polygon pull`. Примеры общие
для всех языков. Удалённый каталог языка условие на Polygon не удаляет —
это делает автор.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state
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
    dialogue_keys,
    digest,
    example_key,
    statement_file_key,
    statement_name_key,
    verify_key,
)
from orchestrator.problem_paths import ProblemPathError, TUTORIAL_FILENAME
from orchestrator.spec import Title
from orchestrator.statement_languages import (
    EXAMPLES_DIRNAME,
    STATEMENT_DIRNAME,
    polygon_language,
    statement_languages,
)
from orchestrator.steps.statement_draft import (
    INTERACTION_FILE,
    dialogue_filenames,
    verify_filename,
)

STEP_NAME = "upload_statement"

_TESTSET = "tests"

_TEX_PARAM_NAMES = {
    "legend.tex": "legend",
    "input_format.tex": "input",
    "output_format.tex": "output",
    "notes.tex": "notes",
}

_EXAMPLE_PATTERN = re.compile(r"^example_(\d+)\.txt$")

NAME_FILENAME = "name.txt"
INTERACTION_FILENAME = INTERACTION_FILE
INTERACTION_PARAM = "interaction"
TUTORIAL_PARAM = "tutorial"

_VERIFY_VALUE = b"true"


@dataclass(frozen=True)
class Statement:
    """Условие на одном языке: `code` — каталог `statement/<code>/`,
    `tex_files` — имя файла -> содержимое (`_read_statement_dir`), `name` —
    из `name.txt` (`None` — файла нет)."""

    code: str
    tex_files: dict[str, bytes]
    name: str | None

    @property
    def language(self) -> str:
        """Имя языка в Polygon."""
        return polygon_language(self.code)

    def items(self) -> dict[str, str]:
        """Элементы базы синхронизации: tex-файлы и название (если известно)."""
        items = {
            statement_file_key(filename, self.code): digest(content)
            for filename, content in self.tex_files.items()
        }
        if self.name is not None:
            items[statement_name_key(self.code)] = digest(self.name)
        return items

    @property
    def tutorial_key(self) -> str:
        return statement_file_key(TUTORIAL_FILENAME, self.code)


@dataclass(frozen=True)
class Example:
    """Пример условия: `testIndex`, input теста и необязательные поля теста
    для условия — ключ базы синхронизации -> (параметр `problem.saveTest`,
    значение); есть только у полей, файлы которых есть на диске."""

    index: int
    input: bytes
    fields: dict[str, tuple[str, str]]

    def items(self) -> dict[str, str]:
        return {
            example_key(self.index): digest(self.input),
            **{key: digest(value.encode("utf-8")) for key, (_, value) in self.fields.items()},
        }


def _optional_fields(index: int) -> list[tuple[str, str, str, str]]:
    """Необязательные поля теста примера: (имя файла, ключ базы, параметр
    `problem.saveTest`, значение при очистке)."""
    input_name, output_name = dialogue_filenames(index)
    input_key, output_key = dialogue_keys(index)
    return [
        (input_name, input_key, "testInputForStatements", ""),
        (output_name, output_key, "testOutputForStatements", ""),
        (verify_filename(index), verify_key(index), "verifyInputOutputForStatements", "false"),
    ]


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает условие каждого языка (если какой-то из его tex-файлов или
    название отличаются от базы синхронизации — `problem.saveStatement`
    принимает их только вместе), затем изменившиеся примеры по порядку
    `testIndex`.

    Обе зависимости (`create_problem`, `statement_draft`) проверяются до
    первого сетевого вызова.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    statement_dir = _statement_dir(ctx.problem_id, ctx.problems_dir)
    statements = read_statements(
        statement_dir,
        language=ctx.spec.language,
        interactive=ctx.spec.interactive,
        title=ctx.spec.title,
    )
    examples = _read_examples(statement_dir)

    example_items = _example_items(examples)
    uploaded: dict[str, str | None] = {}
    done: list[str] = []

    for statement in statements:
        statement_items = statement.items()
        # Без базы (`push --force`) неизвестно, что на Polygon, — не очищается ничего.
        tutorial_cleared = (
            TUTORIAL_FILENAME not in statement.tex_files
            and ctx.synced is not None
            and ctx.synced.get(statement.tutorial_key) is not None
        )
        if not (changed_items(ctx, statement_items) or tutorial_cleared):
            continue
        client.call(
            "problem.saveStatement",
            {
                "problemId": str(polygon_id),
                "lang": statement.language,
                "name": statement.name,
                **{
                    _tex_param(filename): content.decode("utf-8")
                    for filename, content in statement.tex_files.items()
                },
                **({TUTORIAL_PARAM: ""} if tutorial_cleared else {}),
            },
        )
        uploaded.update(statement_items)
        if tutorial_cleared:
            uploaded[statement.tutorial_key] = None
        done.append(f"saved {statement.language} statement (name={statement.name!r})")

    changed_examples: dict[str, str | None] = dict(changed_items(ctx, example_items))
    sent_examples = 0
    for example in examples:
        cleared = _cleared_fields(ctx, example)
        if not (set(example.items()) & set(changed_examples) or cleared):
            continue
        params = {
            "problemId": str(polygon_id),
            "testset": _TESTSET,
            "testIndex": str(example.index),
            "testInput": example.input.decode("utf-8"),
            "testUseInStatements": "true",
        }
        params.update(dict(example.fields.values()))
        params.update(dict(cleared.values()))
        client.call("problem.saveTest", params)
        changed_examples.update({key: None for key in cleared})
        sent_examples += 1
    if sent_examples:
        uploaded.update(changed_examples)
        done.append(f"{sent_examples} of {len(examples)} sample test(s)")

    if not uploaded:
        return StepResult(NO_CHANGES_MESSAGE)
    return StepResult(f"{' and '.join(done)} on Polygon id={polygon_id}", uploaded)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`). Пока `name.txt` нет (его
    создаст push из названия в спеке), название не сравнивается."""

    def read_items() -> dict[str, str]:
        statement_dir = _statement_dir(problem_id, problems_dir)
        # Спека здесь нет: interaction.tex сравнивается, если он есть, и
        # обязательного языка нет.
        statements = read_statements(statement_dir)
        items = _example_items(_read_examples(statement_dir))
        state = load_polygon_state(problem_id, problems_dir=problems_dir)
        synced = state.synced if state is not None else {}
        for statement in statements:
            items.update(statement.items())
            # Удалённый разбор push очистит на Polygon — это тоже несинхронизированная правка.
            if statement.tutorial_key not in items and synced.get(statement.tutorial_key):
                items[statement.tutorial_key] = digest(b"")
        return items

    return sync_status(problem_id, problems_dir, key_prefix="statement/", read_items=read_items)


def read_statements(
    statement_dir: Path,
    *,
    language: str | None = None,
    interactive: bool | None = None,
    title: Title | None = None,
) -> list[Statement]:
    """Условие каждого языка `statement/<код>/`, по алфавиту кодов.

    `language` — основной язык: без его каталога — `PolygonStepError`.
    `interactive` — нужен ли `interaction.tex`; `None` (`polygon status`, без
    спека) — читается, если он есть. С `title` (push) недостающий `name.txt`
    создаётся из названия спека на этом языке, а без названия —
    `PolygonStepError`; без `title` название такого условия неизвестно
    (`Statement.name` — `None`).
    """
    try:
        codes = statement_languages(statement_dir)
    except ProblemPathError as exc:
        raise PolygonStepError(str(exc)) from exc
    if language is not None and language not in codes:
        raise PolygonStepError(
            f"directory {statement_dir / language} not found — first run "
            "the generative step statement_draft"
        )
    statements = []
    for code in codes:
        directory = statement_dir / code
        name_path = directory / NAME_FILENAME
        if not name_path.exists() and title is not None:
            default = title.for_language(code)
            if default is None:
                raise PolygonStepError(
                    f"{name_path} not found — write the problem name in "
                    f"{polygon_language(code)!r} into it"
                )
            name_path.write_text(default + "\n", encoding="utf-8")
        statements.append(
            Statement(
                code=code,
                tex_files=_read_statement_dir(
                    directory,
                    interactive=(
                        (directory / INTERACTION_FILENAME).exists()
                        if interactive is None
                        else interactive
                    ),
                    generated=code == language,
                ),
                name=read_name(name_path) if name_path.exists() else None,
            )
        )
    return statements


def read_name(path: Path) -> str:
    """Название задачи из `name.txt` без окружающих пробелов и переводов
    строк — их добавляют редакторы, в название они не входят."""
    name = path.read_text(encoding="utf-8").strip()
    if not name:
        raise PolygonStepError(f"{path} is empty — write the problem name into it")
    return name


def _tex_param(filename: str) -> str:
    return {
        **_TEX_PARAM_NAMES,
        INTERACTION_FILENAME: INTERACTION_PARAM,
        TUTORIAL_FILENAME: TUTORIAL_PARAM,
    }[filename]


def _example_items(examples: list[Example]) -> dict[str, str]:
    return {key: value for example in examples for key, value in example.items().items()}


def _cleared_fields(ctx: StepContext, example: Example) -> dict[str, tuple[str, str]]:
    """Поля теста примера, которые есть в базе синхронизации, но файла
    которых больше нет: ключ -> (параметр, значение при очистке). Без базы
    (`push --force`) неизвестно, что было на Polygon, — не очищается ничего."""
    if ctx.synced is None:
        return {}
    return {
        key: (param, empty)
        for _, key, param, empty in _optional_fields(example.index)
        if key not in example.fields and ctx.synced.get(key) is not None
    }


def _statement_dir(problem_id: str, problems_dir: Path) -> Path:
    return Path(problems_dir) / problem_id / STATEMENT_DIRNAME


def _read_statement_dir(
    statement_dir: Path, *, interactive: bool = False, generated: bool = True
) -> dict[str, bytes]:
    """Содержимое 4 фиксированных tex-файлов (имя файла -> содержимое), у
    интерактивной задачи — ещё `interaction.tex`. Если какого-то нет —
    `PolygonStepError` с его именем, а не пустая строка вместо содержимого.
    Необязательный `tutorial.tex` — только если он есть.
    `generated=False` — каталог не основного языка: его пишет не шаг модели,
    а автор (или `polygon pull`)."""
    names = [*_TEX_PARAM_NAMES, *([INTERACTION_FILENAME] if interactive else [])]
    missing = [name for name in names if not (statement_dir / name).exists()]
    if missing:
        hint = (
            "write them by hand (a statement in another language) or remove the directory"
            if not generated
            else "first run the generative step statement_draft"
        )
        raise PolygonStepError(f"files missing in {statement_dir}: {missing} — {hint}")
    if (statement_dir / TUTORIAL_FILENAME).exists():
        names.append(TUTORIAL_FILENAME)
    return {filename: (statement_dir / filename).read_bytes() for filename in names}


def _read_examples(statement_dir: Path) -> list[Example]:
    """Примеры по возрастанию `testIndex` с полями для условия, файлы которых
    есть (см. `Example`). Флаг verify не `true` — `PolygonStepError`.

    Нумерация `example_<N>.txt` должна быть непрерывной 1..N. Набор файлов
    уже проверяет `statement_draft` перед записью, но этот шаг запускается
    независимо от него, поэтому проверка повторяется здесь.
    """
    examples_dir = statement_dir / EXAMPLES_DIRNAME
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

    examples: list[Example] = []
    for index, content in sorted(found.items()):
        fields: dict[str, tuple[str, str]] = {}
        for filename, key, param, _ in _optional_fields(index):
            path = examples_dir / filename
            if not path.exists():
                continue
            value = path.read_bytes()
            if key == verify_key(index):
                if value.strip().lower() != _VERIFY_VALUE:
                    raise PolygonStepError(
                        f"{path} must contain 'true' — delete the file to turn "
                        "verification of input/output for statements off"
                    )
                value = _VERIFY_VALUE
            fields[key] = (param, value.decode("utf-8"))
        examples.append(Example(index, content, fields))
    return examples


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
