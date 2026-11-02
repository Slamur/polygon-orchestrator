"""Языки условия задачи.

Основной язык условия — поле `language` спека (имя языка в Polygon, по
умолчанию `russian`): на нём пишут шаги модели (`statement_draft`,
`tutorial_draft`), и его условие лежит прямо в `statement/`. Остальные языки
задачи существуют только на уровне Polygon: условие на языке `<lang>` — в
`statement/<lang>/` (те же `.tex`-файлы и `name.txt`, без примеров — примеры
общие для всех языков), его пишет автор или `polygon pull`, а `polygon push`
загружает каждый язык своим `problem.saveStatement`.

Без тяжёлых импортов: модуль читают `polygon status`, `local check` и
дополнение CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from orchestrator.problem_paths import SPEC_FILENAME, find_authoring_file

DEFAULT_LANGUAGE = "russian"

# Языки, на которых умеют писать шаги модели: язык -> ключ `title` спека и
# название языка для промпта. Правила оформления (кавычки и т.п.) в
# `templates/tutorials/` описаны для этих языков.
GENERATION_LANGUAGES = {
    "russian": ("ru", "русский"),
    "english": ("en", "английский"),
}

# Каталог `statement/`, который не язык: примеры общие для всех языков.
EXAMPLES_DIRNAME = "examples"

# Имя языка в Polygon — строчная латиница (`russian`, `english`, `kazakh`).
_LANGUAGE_PATTERN = re.compile(r"^[a-z]+$")


def title_key(language: str) -> str | None:
    """Ключ `title` спека с названием задачи на языке `language`, `None` —
    в спеке названия на этом языке не бывает."""
    known = GENERATION_LANGUAGES.get(language)
    return known[0] if known else None


def language_name(language: str) -> str:
    """Название языка для промпта (`русский`)."""
    return GENERATION_LANGUAGES[language][1]


def is_language_dirname(name: str) -> bool:
    return name != EXAMPLES_DIRNAME and bool(_LANGUAGE_PATTERN.match(name))


def extra_languages(statement_dir: Path) -> list[str]:
    """Дополнительные языки условия — подкаталоги `statement/` с именем
    языка Polygon, по алфавиту. Прочие подкаталоги (`examples/`, имена не
    из строчной латиницы) — не языки."""
    if not statement_dir.is_dir():
        return []
    return sorted(
        path.name for path in statement_dir.iterdir() if path.is_dir() and is_language_dirname(path.name)
    )


def read_spec_language(problem_dir: Path) -> str:
    """Основной язык условия по спеку задачи без его валидации — для команд,
    которым спек не нужен (`polygon status|pull`, `local check|fix`): нет
    спека, он не читается или поле не задано — `DEFAULT_LANGUAGE`."""
    path = find_authoring_file(Path(problem_dir), SPEC_FILENAME)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return DEFAULT_LANGUAGE
    language = raw.get("language") if isinstance(raw, dict) else None
    if isinstance(language, str) and is_language_dirname(language):
        return language
    return DEFAULT_LANGUAGE
