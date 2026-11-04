"""The problem's statement languages.

The statement in each language lies in its own directory `statement/<code>/`
(`statement/ru/`, `statement/en/`): `legend.tex`, `input_format.tex`,
`output_format.tex`, `notes.tex` (+ `interaction.tex`, `tutorial.tex`) and
`name.txt`. The examples are shared by all languages — `statement/examples/`. The language
code is the directory, the key of the spec's `title` and the value of `language`; on Polygon the language
is called by its full name (`POLYGON_LANGUAGES`: `ru` -> `russian`), a language without
a code in the table — a directory with the Polygon name itself.

The main language is the spec's `language` (`ru` by default): the model's steps
(`statement_draft`, `tutorial_draft`) write in it. The other languages exist
only at the Polygon level: they are written by the author or `polygon pull`, and `polygon
push` uploads each language with its own `problem.saveStatement`.

Problems whose statement lies directly in `statement/` (the layout from before languages)
are moved by `migrate_statement_languages` on the first command to the problem.

Without heavy imports: the module is read by the layout migration in `cli.py` and by checks
without a spec.
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

# Language code -> the language's name in Polygon.
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

# The languages the model's steps can write in: code -> the name for the prompt.
# The formatting rules (quotes, etc.) in `templates/tutorials/` are described for them.
GENERATION_LANGUAGES = {
    "ru": "Russian",
    "en": "English",
}

# The `statement/` directory that isn't a language: the examples are shared by all languages.
EXAMPLES_DIRNAME = "examples"

# A language directory — lowercase Latin letters (`ru`, `en`, `kazakh`).
_LANGUAGE_PATTERN = re.compile(r"^[a-z]+$")


def polygon_language(code: str) -> str:
    """The language's name in Polygon by the `statement/<code>/` directory."""
    return POLYGON_LANGUAGES.get(code, code)


def language_code(polygon_name: str) -> str:
    """The `statement/<code>/` directory for the Polygon language `polygon_name`."""
    return _CODES.get(polygon_name, polygon_name)


def language_name(code: str) -> str:
    """The name of a generation language for the prompt."""
    return GENERATION_LANGUAGES[code]


def is_language_dirname(name: str) -> bool:
    return name != EXAMPLES_DIRNAME and bool(_LANGUAGE_PATTERN.match(name))


def statement_languages(statement_dir: Path) -> list[str]:
    """The statement languages — the subdirectories of `statement/` named after a language, in
    alphabetical order. A directory with the full Polygon name of a language that has a code
    (`russian` with `ru`) is a `ProblemPathError`: a language has one directory."""
    if not statement_dir.is_dir():
        return []
    languages = sorted(
        path.name
        for path in statement_dir.iterdir()
        if path.is_dir() and is_language_dirname(path.name)
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
    """The main statement language by the problem's spec without validating it — for
    the layout migration, when the spec isn't parsed yet: no spec, it isn't
    readable or the field isn't set — `DEFAULT_LANGUAGE`."""
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
    """Moves a statement lying directly in `statement/` (the layout from before
    languages) into `statement/<main language>/` and renames its keys in the
    sync base `polygon_state.json` (`statement/legend.tex` ->
    `statement/ru/legend.tex`), so that push doesn't upload the statement again.
    Returns (the language, the moved file names) or `None` if there's nothing
    to move. A file that already exists in the language directory is a `ProblemPathError` before
    any move."""
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
    """The statement keys without a language directory (`statement/<file>`,
    `statement/name`) -> `statement/<language>/...`, together with the stamps."""
    # The state lies in the problem directory: the path inside the root isn't needed here.
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
