"""Проверка оформления LaTeX-условия (`statement/*.tex`) — `orchestrator
<задача> polygon check` — и его механическое исправление — `orchestrator
<задача> polygon fix`. К Polygon не обращается.

Что проверяется:

- **списки в стиле Markdown** — строки, начинающиеся с `- `, `* `, `+ `
  (маркированный список) или `1. `, `1) ` (нумерованный). LaTeX их не
  понимает и печатает как обычный текст; исправление — окружения
  `itemize`/`enumerate` с `\\item` (вложенность — по отступу);
- **кавычки** — условие русскоязычное (язык в `upload_statement` — `russian`),
  и кавычки в нём — `<<`/`>>` (лигатуры «ёлочек» в LaTeX). Прямые `"`,
  типографские `“ ” „`, `«»` и английские LaTeX-кавычки ```` ``…'' ````
  заменяются на `<<…>>`. Кавычки разбираются по абзацам (между пустыми
  строками), вне формул (`$…$`, `$$…$$`, `\\(…\\)`, `\\[…\\]`), вне
  комментариев `%` и без `\\"` (акцент LaTeX). Абзац, где кавычки не
  складываются в пары, не исправляется — только печатается проверкой.

Разбор — построчный и по регулярным выражениям, а не полный парсер LaTeX:
списки внутри `verbatim`-окружений и формул на несколько строк не
различаются (в условиях задач их практически не бывает).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STATEMENT_DIRNAME = "statement"
# Прежние версии файлов, изменённых `polygon fix`: `.fix_backup/<время>/`.
FIX_BACKUP_DIRNAME = ".fix_backup"

KIND_MARKDOWN_LIST = "markdown list"
KIND_QUOTES = "quotes"
KIND_UNPAIRED_QUOTES = "unpaired quotes"

OPEN_QUOTE = "<<"
CLOSE_QUOTE = ">>"

_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+(?P<text>\S.*)$")
_NUMBERED_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>\d{1,3}[.)])[ \t]+(?P<text>\S.*)$")

_ITEMIZE = "itemize"
_ENUMERATE = "enumerate"


@dataclass(frozen=True)
class StatementIssue:
    file: str  # путь относительно каталога задачи (`statement/legend.tex`)
    line: int  # 1-based
    kind: str
    text: str  # строка файла, без перевода строки
    fixable: bool = True

    def format(self) -> str:
        suffix = "" if self.fixable else " (not fixed by 'polygon fix': fix by hand)"
        return f"{self.file}:{self.line}: {self.kind}{suffix}: {self.text.strip()}"


def statement_files(problem_dir: Path) -> list[Path]:
    """`.tex`-файлы условия задачи (`*.tex.removed` в glob не попадают)."""
    statement_dir = problem_dir / STATEMENT_DIRNAME
    if not statement_dir.is_dir():
        return []
    return sorted(path for path in statement_dir.glob("*.tex") if path.is_file())


def check_text(text: str, file: str) -> list[StatementIssue]:
    lines = text.split("\n")
    issues = [
        StatementIssue(file, number, KIND_MARKDOWN_LIST, lines[number - 1])
        for number in _markdown_list_lines(lines)
    ]
    for paragraph in _paragraphs(lines):
        quotes = _quote_tokens(paragraph)
        paired = _pair_quotes(quotes) is not None
        kind = KIND_QUOTES if paired else KIND_UNPAIRED_QUOTES
        reported: set[int] = set()
        for token in quotes:
            if token.line not in reported:
                reported.add(token.line)
                issues.append(StatementIssue(file, token.line, kind, lines[token.line - 1], paired))
    return sorted(issues, key=lambda issue: (issue.line, issue.kind))


def fix_text(text: str) -> str:
    """Списки Markdown -> `itemize`/`enumerate`, кавычки -> `<<…>>` (кроме
    абзацев с непарными кавычками)."""
    lines = _fix_quotes(text.split("\n"))
    return "\n".join(_fix_lists(lines))


# --- списки -------------------------------------------------------------------


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
    stack: list[tuple[int, str]] = []  # открытые окружения: (отступ пунктов, окружение)

    def close(down_to: int) -> None:
        # Пустая строка перед `\end` остаётся после него — как в исходном тексте,
        # где пустая строка отделяла список от следующего абзаца.
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
            # Продолжение пункта: строка с отступом сразу после него.
            out.append(line)
        else:
            close(0)
            out.append(line)
    close(0)
    return out


# --- кавычки ------------------------------------------------------------------


@dataclass(frozen=True)
class _Quote:
    line: int  # 1-based номер строки файла
    column: int  # позиция в строке
    text: str  # `"`, `“`, ``` `` ``` …


_OPENING = {"«", "„", "``"}
_CLOSING = {"»", "”", "''"}
# Прямая кавычка закрывает, если открыта прямой же; `“` — открывающая в
# английском стиле и закрывающая в немецком („…“).
_TOGGLING = {'"': {'"'}, "“": {"„", "“"}}


def _paragraphs(lines: list[str]) -> list[list[tuple[int, str]]]:
    """Абзацы — группы непустых строк: [(1-based номер строки, строка)]."""
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
    """Кавычки абзаца вне формул и комментариев. Формула может продолжаться
    на следующей строке абзаца."""
    tokens: list[_Quote] = []
    math_end: str | None = None  # закрывающий разделитель открытой формулы
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
                i += 2  # команда/экранирование: `\$`, `\%`, `\"`, `\\`
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
    """Для каждой кавычки — открывающая ли она; None, если кавычки абзаца
    не складываются в пары."""
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
        # С конца строки, чтобы позиции ещё не заменённых кавычек не сдвигались.
        for token, is_open in sorted(
            zip(tokens, opening), key=lambda pair: (pair[0].line, pair[0].column), reverse=True
        ):
            line = lines[token.line - 1]
            replacement = OPEN_QUOTE if is_open else CLOSE_QUOTE
            lines[token.line - 1] = (
                line[: token.column] + replacement + line[token.column + len(token.text) :]
            )
    return lines
