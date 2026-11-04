"""Checking the formatting of a LaTeX statement (`statement/<language code>/*.tex`) — `orchestrator
<problem> local check` — and fixing it mechanically — `orchestrator
<problem> local fix`. Doesn't call Polygon.

What is checked:

- **Markdown-style lists** — lines starting with `- `, `* `, `+ `
  (a bulleted list) or `1. `, `1) ` (a numbered one). LaTeX doesn't
  understand them and prints them as ordinary text; the fix — the
  `itemize`/`enumerate` environments with `\\item` (nesting — by indentation);
- **quotes** — only in a Russian statement (`statement/ru/`, see
  `orchestrator/statement_languages.py`): the quotes in it are `<<`/`>>`
  (the LaTeX ligatures for guillemets). Straight `"`,
  typographic `“ ” „`, `«»` and English LaTeX quotes ```` ``…'' ````
  are replaced with `<<…>>`. Quotes are parsed by paragraph (between empty
  lines), outside formulas (`$…$`, `$$…$$`, `\\(…\\)`, `\\[…\\]`), outside
  `%` comments and without `\\"` (a LaTeX accent). A paragraph where the quotes don't
  pair up isn't fixed — it's only printed by the check.

The parsing is line by line and with regular expressions, not a full LaTeX parser:
lists inside `verbatim` environments and multi-line formulas aren't
distinguished (they practically never occur in problem statements).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.statement_languages import (
    STATEMENT_DIRNAME,  # noqa: F401 — part of the module's interface (local_tests, solutions_run)
    statement_languages,
)

# Previous versions of files changed by `local fix`: `.fix_backup/<time>/`.
FIX_BACKUP_DIRNAME = ".fix_backup"

KIND_MARKDOWN_LIST = "markdown list"
KIND_QUOTES = "quotes"
KIND_UNPAIRED_QUOTES = "unpaired quotes"

# The language (code) in which quotes are `<<…>>`.
QUOTES_LANGUAGE = "ru"

OPEN_QUOTE = "<<"
CLOSE_QUOTE = ">>"

_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+(?P<text>\S.*)$")
_NUMBERED_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>\d{1,3}[.)])[ \t]+(?P<text>\S.*)$")

_ITEMIZE = "itemize"
_ENUMERATE = "enumerate"


@dataclass(frozen=True)
class StatementIssue:
    file: str  # the path relative to the problem directory (`statement/ru/legend.tex`)
    line: int  # 1-based
    kind: str
    text: str  # a line of the file, without the line break
    fixable: bool = True

    def format(self) -> str:
        suffix = "" if self.fixable else " (not fixed by 'local fix': fix by hand)"
        return f"{self.file}:{self.line}: {self.kind}{suffix}: {self.text.strip()}"


def statement_files(problem_dir: Path) -> list[tuple[Path, str]]:
    """The problem's statement `.tex` files with the language code of each
    (`statement/<code>/*.tex`), by language. `*.tex.removed` don't match the glob."""
    statement_dir = problem_dir / STATEMENT_DIRNAME
    return [
        (path, code)
        for code in statement_languages(statement_dir)
        for path in sorted((statement_dir / code).glob("*.tex"))
        if path.is_file()
    ]


def check_text(text: str, file: str, *, quotes: bool = True) -> list[StatementIssue]:
    """The formatting problems of `text`; `quotes=False` — without quotes (the statement isn't
    in `QUOTES_LANGUAGE`)."""
    lines = text.split("\n")
    issues = [
        StatementIssue(file, number, KIND_MARKDOWN_LIST, lines[number - 1])
        for number in _markdown_list_lines(lines)
    ]
    for paragraph in _paragraphs(lines) if quotes else []:
        quotes = _quote_tokens(paragraph)
        paired = _pair_quotes(quotes) is not None
        kind = KIND_QUOTES if paired else KIND_UNPAIRED_QUOTES
        reported: set[int] = set()
        for token in quotes:
            if token.line not in reported:
                reported.add(token.line)
                issues.append(StatementIssue(file, token.line, kind, lines[token.line - 1], paired))
    return sorted(issues, key=lambda issue: (issue.line, issue.kind))


def fix_text(text: str, *, quotes: bool = True) -> str:
    """Markdown lists -> `itemize`/`enumerate`, quotes -> `<<…>>` (except
    paragraphs with unpaired quotes; `quotes=False` — quotes aren't touched)."""
    lines = text.split("\n")
    if quotes:
        lines = _fix_quotes(lines)
    return "\n".join(_fix_lists(lines))


# --- lists --------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    indent: int
    env: str
    text: str


def _list_item(line: str) -> _Item | None:
    for regex, env in ((_BULLET_RE, _ITEMIZE), (_NUMBERED_RE, _ENUMERATE)):
        match = regex.match(line)
        if match:
            indent = len(match.group("indent").expandtabs(4))
            return _Item(indent, env, match.group("text"))
    return None


def _markdown_list_lines(lines: list[str]) -> list[int]:
    return [number for number, line in enumerate(lines, 1) if _list_item(line)]


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _fix_lists(lines: list[str]) -> list[str]:
    out: list[str] = []
    stack: list[tuple[int, str]] = []  # open environments: (item indentation, environment)

    def close(down_to: int) -> None:
        # An empty line before `\end` stays after it — as in the source text,
        # where an empty line separated the list from the next paragraph.
        trailing: list[str] = []
        while out and not out[-1].strip() and stack:
            trailing.append(out.pop())
        while len(stack) > down_to:
            indent, env = stack.pop()
            out.append(" " * indent + f"\\end{{{env}}}")
        out.extend(trailing)

    for line in lines:
        item = _list_item(line)
        if item is not None:
            while stack and stack[-1][0] > item.indent:
                close(len(stack) - 1)
            if stack and stack[-1][0] == item.indent and stack[-1][1] != item.env:
                close(len(stack) - 1)
            if not stack or stack[-1][0] < item.indent:
                stack.append((item.indent, item.env))
                out.append(" " * item.indent + f"\\begin{{{item.env}}}")
            out.append(" " * item.indent + f"\\item {item.text}")
        elif not line.strip():
            out.append(line)
        elif stack and _indent(line) > stack[-1][0] and (out and out[-1].strip()):
            # A continuation of an item: an indented line right after it.
            out.append(line)
        else:
            close(0)
            out.append(line)
    close(0)
    return out


# --- quotes -------------------------------------------------------------------


@dataclass(frozen=True)
class _Quote:
    line: int  # 1-based line number in the file
    column: int  # the position in the line
    text: str  # `"`, `“`, ``` `` ``` …


_OPENING = {"«", "„", "``"}
_CLOSING = {"»", "”", "''"}
# A straight quote closes if it was opened by a straight one too; `“` is opening in
# the English style and closing in the German one („…“).
_TOGGLING = {'"': {'"'}, "“": {"„", "“"}}


def _paragraphs(lines: list[str]) -> list[list[tuple[int, str]]]:
    """Paragraphs — groups of non-empty lines: [(1-based line number, line)]."""
    paragraphs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        if line.strip():
            current.append((number, line))
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def _quote_tokens(paragraph: list[tuple[int, str]]) -> list[_Quote]:
    """A paragraph's quotes outside formulas and comments. A formula may continue
    on the next line of the paragraph."""
    tokens: list[_Quote] = []
    math_end: str | None = None  # the closing delimiter of an open formula
    for number, line in paragraph:
        i = 0
        while i < len(line):
            two = line[i : i + 2]
            ch = line[i]
            if ch == "\\":
                if math_end is not None and two == math_end:
                    math_end = None
                elif math_end is None and two in ("\\(", "\\["):
                    math_end = "\\)" if two == "\\(" else "\\]"
                i += 2  # a command/escape: `\$`, `\%`, `\"`, `\\`
                continue
            if ch == "%":
                break
            if ch == "$":
                delimiter = "$$" if two == "$$" else "$"
                if math_end is None:
                    math_end = delimiter
                elif math_end == delimiter:
                    math_end = None
                i += len(delimiter)
                continue
            if math_end is None:
                if two in ("``", "''"):
                    tokens.append(_Quote(number, i, two))
                    i += 2
                    continue
                if ch in _OPENING or ch in _CLOSING or ch in _TOGGLING:
                    tokens.append(_Quote(number, i, ch))
            i += 1
    return tokens


def _pair_quotes(tokens: list[_Quote]) -> list[bool] | None:
    """For each quote — whether it's an opening one; None if the paragraph's quotes
    don't pair up."""
    stack: list[str] = []
    opening: list[bool] = []
    for token in tokens:
        text = token.text
        if text in _TOGGLING:
            is_open = not (stack and stack[-1] in _TOGGLING[text])
        else:
            is_open = text in _OPENING
        if is_open:
            stack.append(text)
        elif not stack:
            return None
        else:
            stack.pop()
        opening.append(is_open)
    return None if stack else opening


def _fix_quotes(lines: list[str]) -> list[str]:
    lines = list(lines)
    for paragraph in _paragraphs(lines):
        tokens = _quote_tokens(paragraph)
        opening = _pair_quotes(tokens)
        if not tokens or opening is None:
            continue
        # From the end of the line, so that the positions of quotes not yet replaced don't shift.
        for token, is_open in sorted(
            zip(tokens, opening, strict=True),
            key=lambda pair: (pair[0].line, pair[0].column),
            reverse=True,
        ):
            line = lines[token.line - 1]
            replacement = OPEN_QUOTE if is_open else CLOSE_QUOTE
            lines[token.line - 1] = (
                line[: token.column] + replacement + line[token.column + len(token.text) :]
            )
    return lines
