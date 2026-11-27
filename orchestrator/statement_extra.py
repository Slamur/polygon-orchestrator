"""The `\\Extra{<text>}` command of a statement: the `problem.tex` a problem needs when its
statement uses it.

The command's definition is `templates/extra.tex`. Polygon builds the statement only with the
statement files it knows (`olymp.sty`, `problem.tex`, `statements.ftl`) — another resource
file isn't in the build directory — so the definition goes into the problem's `problem.tex`
(Polygon's FreeMarker template of one problem's statement; FreeMarker leaves TeX's `#1`
alone), as a block between the `EXTRA_BEGIN` and `EXTRA_END` lines at its start.

A problem whose statement (`statement/<code>/*.tex`, any language) has `\\Extra` gets its own
`problem.tex` (`problems/<problem path>/resources/`, see `orchestrator/resources.py`): its
own version if it has one, else the default (`resources/polygon-statement/problem.tex`),
with the block put first — or, if the block is there, replaced by the template's current
one. `upload_resources` (`polygon push`) ensures it before uploading the problem's
resources. A problem whose statement has no `\\Extra` is left alone, and the block of a
problem that stopped using it stays (it changes nothing without a call).
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.resources import STATEMENT_RESOURCES_DIR, problem_resources_dir
from orchestrator.statement_languages import STATEMENT_DIRNAME, statement_languages

EXTRA_TEMPLATE_FILENAME = "extra.tex"
PROBLEM_TEX_FILENAME = "problem.tex"
EXTRA_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
# The lines around the definition's block in `problem.tex`.
EXTRA_BEGIN = "% \\Extra: begin (templates/extra.tex)"
EXTRA_END = "% \\Extra: end"

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
    contents): empty if the statement doesn't use it or `problem.tex` is already as needed.
    No template `extra.tex` — `FileNotFoundError`."""
    if not uses_extra(problem_dir):
        return {}
    template = Path(templates_dir) / EXTRA_TEMPLATE_FILENAME
    if not template.is_file():
        raise FileNotFoundError(f"template {template} not found — check templates_dir")

    own = problem_resources_dir(Path(problem_dir)) / PROBLEM_TEX_FILENAME
    source = own if own.is_file() else STATEMENT_RESOURCES_DIR / PROBLEM_TEX_FILENAME
    content = source.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in content else "\n"
    definition = template.read_text(encoding="utf-8").splitlines()
    block = newline.join([EXTRA_BEGIN, *definition, EXTRA_END]) + newline

    lines = content.splitlines(keepends=True)
    stripped = [line.rstrip("\r\n") for line in lines]
    if EXTRA_BEGIN in stripped and EXTRA_END in stripped[stripped.index(EXTRA_BEGIN) :]:
        begin = stripped.index(EXTRA_BEGIN)
        end = stripped.index(EXTRA_END, begin)
        updated = "".join(lines[:begin]) + block + "".join(lines[end + 1 :])
    else:
        updated = block + content
    if own.is_file() and updated == content:
        return {}
    return {PROBLEM_TEX_FILENAME: updated.encode("utf-8")}


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
