"""The `\\Extra{<text>}` command of a statement: the resource files a problem needs when its
statement uses it.

The command is defined in `templates/extra.tex`; the problem's `problem.tex` (Polygon's
FreeMarker template of one problem's statement) loads it with `\\input{extra.tex}`. A problem
whose statement (`statement/<code>/*.tex`, any language) has `\\Extra` gets both files as
its own resources (`problems/<problem path>/resources/`, see `orchestrator/resources.py`):

- `extra.tex` — copied from the template if the problem has none (a problem's own version
  is kept as is, like `problem_lib.h`);
- `problem.tex` — its own version if it has one, else the default
  (`resources/polygon-statement/problem.tex`), with the `\\input{extra.tex}` line put
  first if it isn't there yet.

`upload_resources` (`polygon push`) ensures them before uploading the problem's resources.
A problem whose statement has no `\\Extra` is left alone, and the files of a problem that
stopped using it stay (they change nothing without a call).
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.resources import (
    STATEMENT_RESOURCES_DIR,
    problem_resources_dir,
)
from orchestrator.statement_languages import STATEMENT_DIRNAME, statement_languages

EXTRA_FILENAME = "extra.tex"
PROBLEM_TEX_FILENAME = "problem.tex"
EXTRA_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
# The line of `problem.tex` that loads the command's definition.
EXTRA_INPUT_LINE = "\\input{extra.tex}%"

# `\Extra` as a command (not a prefix of a longer name), outside a `%` comment.
_EXTRA_COMMAND_RE = re.compile(r"\\Extra(?![A-Za-z@])")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")


def uses_extra(problem_dir: Path) -> bool:
    """Whether a statement `.tex` file of the problem, in any language, calls `\\Extra`."""
    statement_dir = Path(problem_dir) / STATEMENT_DIRNAME
    for code in statement_languages(statement_dir):
        for path in sorted((statement_dir / code).glob("*.tex")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if _EXTRA_COMMAND_RE.search(_COMMENT_RE.sub("", line)):
                    return True
    return False


def extra_resources(
    problem_dir: Path, *, templates_dir: Path = EXTRA_TEMPLATES_DIR
) -> dict[str, bytes]:
    """The resource files to write into the problem's `resources/` for `\\Extra` (name ->
    contents): empty if the statement doesn't use it or the files are already as needed.
    No template `extra.tex` — `FileNotFoundError`."""
    if not uses_extra(problem_dir):
        return {}
    resources_dir = problem_resources_dir(Path(problem_dir))
    result: dict[str, bytes] = {}

    if not (resources_dir / EXTRA_FILENAME).is_file():
        template = Path(templates_dir) / EXTRA_FILENAME
        if not template.is_file():
            raise FileNotFoundError(f"template {template} not found — check templates_dir")
        result[EXTRA_FILENAME] = template.read_bytes()

    own = resources_dir / PROBLEM_TEX_FILENAME
    source = own if own.is_file() else STATEMENT_RESOURCES_DIR / PROBLEM_TEX_FILENAME
    content = source.read_bytes()
    if not _has_input_line(content):
        newline = b"\r\n" if b"\r\n" in content else b"\n"
        result[PROBLEM_TEX_FILENAME] = EXTRA_INPUT_LINE.encode() + newline + content
    elif source != own:
        result[PROBLEM_TEX_FILENAME] = content
    return result


def ensure_extra_resources(
    problem_dir: Path, *, templates_dir: Path = EXTRA_TEMPLATES_DIR
) -> list[str]:
    """Writes `extra_resources` into the problem's `resources/`; the names written."""
    files = extra_resources(problem_dir, templates_dir=templates_dir)
    if not files:
        return []
    resources_dir = problem_resources_dir(Path(problem_dir))
    resources_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (resources_dir / name).write_bytes(content)
    return sorted(files)


def _has_input_line(content: bytes) -> bool:
    text = content.decode("utf-8", errors="replace")
    return any(
        _COMMENT_RE.sub("", line).strip().startswith("\\input{extra.tex}")
        for line in text.splitlines()
    )
