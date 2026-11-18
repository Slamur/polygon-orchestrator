"""Checking that the subtasks table of the statement (`statement/<code>/scoring.tex`, the
Scoring block) matches the problem's test groups (`test_groups` of `constraints.yaml`,
`orchestrator/test_groups.py`) — `orchestrator <problem> local check`. Doesn't call the
model or Polygon.

What is checked, in each statement language:

- a problem with groups has `scoring.tex` in every language that has a statement;
  `constraints.yaml` without groups and a `scoring.tex` — a stale table;
- the table (the first `tabular` of the file) has one row per group, in the groups'
  order: a row is a data row if its first cell is a number (the header rows aren't);
  a row of group `0` (the statement examples) with 0 points is allowed and skipped;
- the second cell is the group's points (if `constraints.yaml` sets them);
- the third cell mentions the group's extra limits: the numeric `min`/`max` of the
  group's `variables` and of each constant in its `validator.groups` entry — each value
  must occur among the numbers of the cell (`10`, `10^{12}`, `2 \\cdot 10^5`,
  `10\\,000`); which variable the number bounds isn't checked, and neither are
  expressions and text (`special_property`);
- the fourth cell, if there is one, — exactly the group's `dependencies` (numbers in any
  order).

The fifth cell (the feedback policy) is words in the statement's language and isn't
checked. The parsing is regular expressions with brace depth, not a LaTeX parser: rows
are split by `\\\\` and cells by `&` outside braces, `%` comments are dropped.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import yaml

from orchestrator.statement_languages import STATEMENT_DIRNAME, statement_languages
from orchestrator.test_groups import (
    EXAMPLES_GROUP,
    TestGroupsError,
    constraints_path,
    load_test_groups,
    with_string_names,
)

SCORING_FILENAME = "scoring.tex"

_COMMENT = re.compile(r"(?<!\\)%.*")
_BEGIN_TABULAR = re.compile(r"\\begin\{tabular\*?\}")
_END_TABULAR = re.compile(r"\\end\{tabular\*?\}")
_RULES = re.compile(r"\\(?:hline|cline\{[^}]*\}|toprule|midrule|bottomrule)")
# Thousands separators inside a number: `10\,000`, `10{,}000`, `10~000`, `10\ 000`.
_DIGIT_SEPARATOR = re.compile(r"(?<=\d)(?:\\,|\{,\}|~|\\ |\\thinspace\s*)(?=\d{3}(?!\d))")
_NUMBER = re.compile(
    r"(?P<mantissa>\d+(?:\.\d+)?)\s*(?:\\cdot|\\times)\s*10\s*\^\s*\{?\s*(?P<exp1>\d+)\s*\}?"
    r"|10\s*\^\s*\{?\s*(?P<exp2>\d+)\s*\}?"
    r"|(?P<plain>\d+)"
)
_CELL_NOISE = re.compile(r"\\[A-Za-z]+\*?|[{}$\s]")


def check_scoring(problem_dir: Path) -> list[str]:
    """Mismatches between the `scoring.tex` files of the problem and its test groups,
    one message per finding (the file path relative to the problem directory first).
    No `constraints.yaml` and no `scoring.tex` — nothing to check."""
    problem_dir = Path(problem_dir)
    statement_dir = problem_dir / STATEMENT_DIRNAME
    languages = [
        code
        for code in statement_languages(statement_dir)
        if any((statement_dir / code).glob("*.tex"))
    ]
    files = {code: statement_dir / code / SCORING_FILENAME for code in languages}
    present = {code: path for code, path in files.items() if path.is_file()}
    try:
        groups = load_test_groups(problem_dir)
    except TestGroupsError as exc:
        return [f"{SCORING_FILENAME} isn't checked: {exc}"] if present else []
    if groups is None:
        if not present or not constraints_path(problem_dir).is_file():
            return []
        return [
            f"{_relative(path, problem_dir)}: constraints.yaml has no test_groups — "
            "a table of subtasks the problem doesn't have"
            for path in present.values()
        ]
    errors = [
        f"{_relative(path, problem_dir)}: missing — the problem has test groups"
        for code, path in files.items()
        if code not in present
    ]
    limits = _group_limits(problem_dir)
    for path in present.values():
        where = _relative(path, problem_dir)
        rows = table_rows(path.read_text(encoding="utf-8"))
        if rows is None:
            errors.append(f"{where}: no tabular with the subtasks")
            continue
        errors += [f"{where}: {message}" for message in _compare(rows, groups, limits)]
    return errors


def table_rows(text: str) -> list[list[str]] | None:
    """The cells of the data rows of the first `tabular` (a row whose first cell is a
    number); `None` — no `tabular`."""
    text = "\n".join(_COMMENT.sub("", line) for line in text.split("\n"))
    begin = _BEGIN_TABULAR.search(text)
    if begin is None:
        return None
    body_start = _skip_group(text, _skip_group(text, begin.end(), optional=True))
    end = _END_TABULAR.search(text, body_start)
    body = text[body_start : end.start() if end else len(text)]
    rows = []
    for row in _split_top_level(body.replace("\\tabularnewline", "\\\\"), "\\\\"):
        cells = [cell.strip() for cell in _split_top_level(_RULES.sub("", row), "&")]
        if cells and _plain(cells[0]).isdigit():
            rows.append(cells)
    return rows


def numbers(cell: str) -> set[int]:
    """The integer values written in a LaTeX cell: `10`, `10^{12}`, `2 \\cdot 10^5`,
    `10\\,000`."""
    text = cell
    while (replaced := _DIGIT_SEPARATOR.sub("", text)) != text:
        text = replaced
    values = set()
    for match in _NUMBER.finditer(text):
        if match.group("exp1") is not None:
            value = Fraction(match.group("mantissa")) * 10 ** int(match.group("exp1"))
        elif match.group("exp2") is not None:
            value = Fraction(10 ** int(match.group("exp2")))
        else:
            value = Fraction(int(match.group("plain")))
        if value.denominator == 1:
            values.add(int(value))
    return values


def _compare(rows: list[list[str]], groups, limits: dict[str, set[int]]) -> list[str]:
    errors = []
    rows = [row for row in rows if not (_plain(row[0]) == EXAMPLES_GROUP and _points(row) == 0)]
    names = [_plain(row[0]) for row in rows]
    expected = [group.name for group in groups]
    if names != expected:
        errors.append(
            f"subtasks {', '.join(names) or 'none'} in the table, expected {', '.join(expected)}"
        )
    by_name = {_plain(row[0]): row for row in rows}
    for group in groups:
        row = by_name.get(group.name)
        if row is None:
            continue
        label = f"subtask {group.name}"
        if group.points is not None and _points(row) != group.points:
            cell = row[1] if len(row) > 1 else ""
            errors.append(f"{label}: points {cell or 'none'}, expected {group.points}")
        wanted = set(limits.get(group.name, set()))
        for limit in (group.variables or {}).values():
            wanted.update(
                value
                for value in (limit.min, limit.max)
                if isinstance(value, int) and not isinstance(value, bool)
            )
        found = numbers(row[2]) if len(row) > 2 else set()
        missing = sorted(wanted - found)
        if missing:
            values = ", ".join(str(value) for value in missing)
            errors.append(f"{label}: the constraints don't mention {values} ({_cell(row, 2)})")
        if len(row) > 3:
            dependencies = {str(value) for value in numbers(row[3])}
            if dependencies != set(group.dependencies or []):
                expected_deps = ", ".join(group.dependencies or []) or "none"
                errors.append(
                    f"{label}: necessary subtasks {_cell(row, 3)}, expected {expected_deps}"
                )
    return errors


def _group_limits(problem_dir: Path) -> dict[str, set[int]]:
    """The `min` and `max` of each constant of `validator.groups` in `constraints.yaml`,
    by group."""
    try:
        data = yaml.safe_load(constraints_path(problem_dir).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    validator = data.get("validator") if isinstance(data, dict) else None
    raw = validator.get("groups") if isinstance(validator, dict) else None
    if not isinstance(raw, list):
        return {}
    limits: dict[str, set[int]] = {}
    for group in with_string_names(item for item in raw if isinstance(item, dict)):
        values = limits.setdefault(str(group.get("name")), set())
        for constant in group.get("constants") or []:
            if not isinstance(constant, dict):
                continue
            for value in (constant.get("min"), constant.get("max")):
                if isinstance(value, str) and value.strip().isdigit():
                    value = int(value)
                if isinstance(value, int) and not isinstance(value, bool):
                    values.add(value)
    return limits


def _points(row: list[str]) -> int | None:
    plain = _plain(row[1]) if len(row) > 1 else ""
    return int(plain) if plain.isdigit() else None


def _plain(cell: str) -> str:
    return _CELL_NOISE.sub("", cell)


def _cell(row: list[str], index: int) -> str:
    return f"'{' '.join(row[index].split())}'" if row[index].strip() else "empty"


def _skip_group(text: str, position: int, *, optional: bool = False) -> int:
    """The position after the `{...}` (or `[...]` if `optional`) starting at `position`
    (after whitespace); no such group — `position`."""
    index = position
    while index < len(text) and text[index].isspace():
        index += 1
    opening, closing = ("[", "]") if optional else ("{", "}")
    if index >= len(text) or text[index] != opening:
        return position
    depth = 0
    for current in range(index, len(text)):
        char = text[current]
        if char == "\\":
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return current + 1
    return len(text)


def _split_top_level(text: str, separator: str) -> list[str]:
    """`text` split by `separator` outside braces; `\\&` isn't a separator."""
    parts = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        if text.startswith(separator, index) and depth == 0:
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(depth - 1, 0)
        index += 1
    parts.append(text[start:])
    return parts


def _relative(path: Path, problem_dir: Path) -> str:
    return path.relative_to(problem_dir).as_posix()
