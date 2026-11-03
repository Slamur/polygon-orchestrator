"""Языки условия задачи.

Условие на каждом языке лежит в своём каталоге `statement/<код>/`
(`statement/ru/`, `statement/en/`): `legend.tex`, `input_format.tex`,
`output_format.tex`, `notes.tex` (+ `interaction.tex`, `tutorial.tex`) и
`name.txt`. Примеры общие для всех языков — `statement/examples/`. Код
языка — каталог, ключ `title` спека и значение `language`; на Polygon язык
называется полным именем (`POLYGON_LANGUAGES`: `ru` -> `russian`), язык без
кода в таблице — каталог с самим именем Polygon.

Основной язык — `language` спека (по умолчанию `ru`): на нём пишут шаги
модели (`statement_draft`, `tutorial_draft`). Остальные языки существуют
только на уровне Polygon: их пишет автор или `polygon pull`, а `polygon
push` загружает каждый язык своим `problem.saveStatement`.

Задачи, у которых условие лежит прямо в `statement/` (раскладка до языков),
переносит `migrate_statement_languages` при первой команде к задаче.

Без тяжёлых импортов: модуль читают перенос раскладки в `cli.py` и проверки
без спека.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from orchestrator.polygon.state import load_polygon_state, save_polygon_state
from orchestrator.problem_paths import (
    SPEC_FILENAME,
    ProblemPathError,
    find_authoring_file,
)

STATEMENT_DIRNAME = "statement"
DEFAULT_LANGUAGE = "ru"

# Код языка -> имя языка в Polygon.
POLYGON_LANGUAGES = {
    "ru": "russian",
    "en": "english",
    "uk": "ukrainian",
    "be": "belarusian",
    "kk": "kazakh",
    "ky": "kyrgyz",
    "uz": "uzbek",
    "tg": "tajik",
    "tk": "turkmen",
    "az": "azerbaijani",
    "hy": "armenian",
    "ka": "georgian",
    "tr": "turkish",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "pt": "portuguese",
    "it": "italian",
    "pl": "polish",
    "ro": "romanian",
    "zh": "chinese",
    "ja": "japanese",
    "ko": "korean",
    "vi": "vietnamese",
}
_CODES = {name: code for code, name in POLYGON_LANGUAGES.items()}

# Языки, на которых умеют писать шаги модели: код -> название для промпта.
# Правила оформления (кавычки и т.п.) в `templates/tutorials/` описаны для них.
GENERATION_LANGUAGES = {
    "ru": "русский",
    "en": "английский",
}

# Каталог `statement/`, который не язык: примеры общие для всех языков.
EXAMPLES_DIRNAME = "examples"

# Каталог языка — строчная латиница (`ru`, `en`, `kazakh`).
_LANGUAGE_PATTERN = re.compile(r"^[a-z]+$")


def polygon_language(code: str) -> str:
    """Имя языка в Polygon по каталогу `statement/<code>/`."""
    return POLYGON_LANGUAGES.get(code, code)


def language_code(polygon_name: str) -> str:
    """Каталог `statement/<код>/` для языка Polygon `polygon_name`."""
    return _CODES.get(polygon_name, polygon_name)


def language_name(code: str) -> str:
    """Название языка генерации для промпта (`русский`)."""
    return GENERATION_LANGUAGES[code]


def is_language_dirname(name: str) -> bool:
    return name != EXAMPLES_DIRNAME and bool(_LANGUAGE_PATTERN.match(name))


def statement_languages(statement_dir: Path) -> list[str]:
    """Языки условия — подкаталоги `statement/` с именем языка, по
    алфавиту. Каталог с полным именем Polygon языка, у которого есть код
    (`russian` при `ru`), — `ProblemPathError`: у языка один каталог."""
    if not statement_dir.is_dir():
        return []
    languages = sorted(
        path.name for path in statement_dir.iterdir() if path.is_dir() and is_language_dirname(path.name)
    )
    misnamed = [name for name in languages if name in _CODES]
    if misnamed:
        raise ProblemPathError(
            ", ".join(f"{statement_dir / name}" for name in misnamed)
            + ": a statement language directory is named by its code "
            + ", ".join(f"({_CODES[name]} for {name})" for name in misnamed)
            + " — rename it"
        )
    return languages


def read_spec_language(problem_dir: Path) -> str:
    """Основной язык условия по спеку задачи без его валидации — для
    переноса раскладки, когда спек ещё не разобран: нет спека, он не
    читается или поле не задано — `DEFAULT_LANGUAGE`."""
    path = find_authoring_file(Path(problem_dir), SPEC_FILENAME)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return DEFAULT_LANGUAGE
    language = raw.get("language") if isinstance(raw, dict) else None
    if isinstance(language, str) and is_language_dirname(language):
        return language
    return DEFAULT_LANGUAGE


def migrate_statement_languages(problem_dir: Path) -> tuple[str, list[str]] | None:
    """Переносит условие, лежащее прямо в `statement/` (раскладка до
    языков), в `statement/<основной язык>/` и переименовывает его ключи в
    базе синхронизации `polygon_state.json` (`statement/legend.tex` ->
    `statement/ru/legend.tex`), чтобы push не загрузил условие заново.
    Возвращает (язык, перенесённые имена файлов) или `None`, если переносить
    нечего. Файл, который уже есть в каталоге языка, — `ProblemPathError` до
    любого переноса."""
    statement_dir = Path(problem_dir) / STATEMENT_DIRNAME
    if not statement_dir.is_dir():
        return None
    files = sorted(path for path in statement_dir.iterdir() if path.is_file())
    if not files:
        return None
    language = read_spec_language(problem_dir)
    target_dir = statement_dir / language
    clashes = [path.name for path in files if (target_dir / path.name).exists()]
    if clashes:
        raise ProblemPathError(
            f"{statement_dir}: {clashes} are also in {target_dir} — keep the actual "
            f"ones in {target_dir} and delete the others"
        )
    target_dir.mkdir(exist_ok=True)
    for path in files:
        path.rename(target_dir / path.name)
    _migrate_sync_keys(Path(problem_dir), language)
    return language, [path.name for path in files]


def _migrate_sync_keys(problem_dir: Path, language: str) -> None:
    """Ключи условия без каталога языка (`statement/<файл>`,
    `statement/name`) -> `statement/<language>/...`, вместе с отметками."""
    # Состояние лежит в каталоге задачи: путь внутри корня здесь не нужен.
    problems_dir, problem_id = problem_dir.parent, problem_dir.name
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        return

    def renamed(key: str) -> str:
        parts = key.split("/")
        if len(parts) == 2 and parts[0] == STATEMENT_DIRNAME and parts[1] != EXAMPLES_DIRNAME:
            return f"{STATEMENT_DIRNAME}/{language}/{parts[1]}"
        return key

    state.synced = {renamed(key): value for key, value in state.synced.items()}
    state.stamps = {renamed(key): value for key, value in state.stamps.items()}
    save_polygon_state(problem_id, state, problems_dir=problems_dir)
