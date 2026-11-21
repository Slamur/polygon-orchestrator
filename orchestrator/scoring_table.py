"""The statement's subtasks table (`statement/<code>/scoring.tex`, the Scoring block) —
assembled mechanically by the `scoring_table` step.

Everything in the table but the constraints of a subtask follows from the groups of
`constraints.yaml` and the number of tests of each group: the subtask's name, its points
(for an `each_test` group — with the points of one test, `30 (2)`), its necessary
subtasks, the last column (by `feedback_policy`, or the "points per test" mark of an
`each_test` group) and the paragraphs before the table. The constraints cell is statement
text: the `statement_draft` model writes it into `authoring/scoring.yaml`
(`parse_scoring_texts`), `null` — the subtask has no extra constraints.

Only the generation languages (`statement_languages.GENERATION_LANGUAGES`) have the
fixed words; the tables of other languages are the author's.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from orchestrator.problem_paths import SCORING_TEXTS_FILENAME
from orchestrator.spec import EACH_TEST

SCORING_TEXTS_KEY = "constraints"


class ScoringTableError(ValueError):
    """The table can't be assembled: `scoring.yaml` is invalid or doesn't match the groups,
    or the number of tests of an `each_test` group is unknown or doesn't divide its
    points."""


@dataclass(frozen=True)
class ScoringGroup:
    """What the table needs of a group of `constraints.yaml`."""

    name: str
    points: int | None
    dependencies: tuple[str, ...]
    feedback_policy: str
    points_policy: str


@dataclass(frozen=True)
class _Words:
    total: str  # "{points} {unit}" are substituted
    complete_group: str  # every subtask complete_group
    all_each_test: str  # every subtask each_test
    some_each_test: str  # some subtasks each_test
    header: str
    no_constraints: str
    each_test_mark: str
    feedback: dict[str, str]


_HEADER_RU = r"""\textbf{Подзадача} &
\textbf{Баллы} &
\parbox{5cm}{\centering \textbf{Ограничения}} &
\parbox{3cm}{\textbf{\centering\\Необходимые\\подзадачи\\\vspace{2mm}}} &
\parbox{3cm}{\textbf{\centering\\Информация\\о проверке\\\vspace{2mm}}}"""

_HEADER_EN = r"""\textbf{Subtask} &
\textbf{Points} &
\parbox{5cm}{\centering \textbf{Constraints}} &
\parbox{3cm}{\textbf{\centering\\Necessary\\subtasks\\\vspace{2mm}}} &
\parbox{3cm}{\textbf{\centering\\Verification\\information\\\vspace{2mm}}}"""

_WORDS = {
    "ru": _Words(
        total="Задача оценивается в {points} {unit} (задача состоит из подзадач).",
        complete_group=(
            "Баллы за каждую подзадачу начисляются только в случае, если все тесты для этой "
            "подзадачи\nи необходимых подзадач успешно пройдены."
        ),
        all_each_test=(
            "Подзадача проверяется, если пройдены все тесты необходимых подзадач.\n"
            "Каждый тест подзадачи оценивается независимо в указанное число баллов."
        ),
        some_each_test=(
            "Баллы за подзадачу начисляются только в случае, если все тесты для этой "
            "подзадачи\nи необходимых подзадач успешно пройдены. Исключение~--- подзадачи с "
            "пометкой <<баллы за каждый тест>>:\nтакая подзадача проверяется, если пройдены все "
            "тесты необходимых подзадач,\nи каждый её тест оценивается независимо в указанное "
            "в скобках число баллов."
        ),
        header=_HEADER_RU,
        no_constraints="Без дополнительных ограничений",
        each_test_mark="баллы за каждый тест",
        feedback={
            "icpc": "первая ошибка",
            "points": "только баллы",
            "complete": "все тесты",
            "none": "нет",
        },
    ),
    "en": _Words(
        total="The problem is scored out of {points} {unit} (the problem consists of subtasks).",
        complete_group=(
            "Points for a subtask are awarded only if all tests of this subtask\n"
            "and of the necessary subtasks are passed."
        ),
        all_each_test=(
            "A subtask is tested if all tests of the necessary subtasks are passed.\n"
            "Each test of a subtask is scored independently with the given number of points."
        ),
        some_each_test=(
            "Points for a subtask are awarded only if all tests of this subtask\n"
            "and of the necessary subtasks are passed. The exception is the subtasks marked "
            "``points per test'':\nsuch a subtask is tested if all tests of the necessary "
            "subtasks are passed,\nand each of its tests is scored independently with the "
            "number of points given in parentheses."
        ),
        header=_HEADER_EN,
        no_constraints="No additional constraints",
        each_test_mark="points per test",
        feedback={
            "icpc": "first error",
            "points": "points only",
            "complete": "all tests",
            "none": "none",
        },
    ),
}

SCORING_LANGUAGES = tuple(_WORDS)


def _points_unit(language: str, points: int) -> str:
    if language == "ru":
        if points % 10 == 1 and points % 100 != 11:
            return "балл"
        if points % 10 in (2, 3, 4) and points % 100 not in (12, 13, 14):
            return "балла"
        return "баллов"
    return "point" if points == 1 else "points"


def parse_scoring_texts(text: str, group_names: list[str]) -> dict[str, str | None]:
    """`scoring.yaml`: `constraints` — a mapping of every group's name to the text of its
    constraints cell (LaTeX in the statement's language) or `null` (no extra
    constraints). Exactly the given groups. `ScoringTableError` with every problem found."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScoringTableError(f"{SCORING_TEXTS_FILENAME} is not valid YAML: {exc}") from exc
    texts = data.get(SCORING_TEXTS_KEY) if isinstance(data, dict) else None
    if not isinstance(texts, dict):
        raise ScoringTableError(
            f"{SCORING_TEXTS_FILENAME}: expected a mapping '{SCORING_TEXTS_KEY}' of the "
            "subtasks' names to their constraints"
        )
    result: dict[str, str | None] = {}
    errors: list[str] = []
    for name, value in texts.items():
        if value is not None and not isinstance(value, str):
            errors.append(f"subtask {name}: the constraints must be a string or null")
            continue
        result[str(name)] = (value.strip() or None) if isinstance(value, str) else None
    missing = [name for name in group_names if name not in result]
    extra = [name for name in result if name not in group_names]
    if missing:
        errors.append(f"no constraints of subtask(s) {', '.join(missing)}")
    if extra:
        errors.append(f"subtask(s) {', '.join(extra)} aren't test groups")
    if errors:
        raise ScoringTableError(f"{SCORING_TEXTS_FILENAME}: " + "; ".join(errors))
    return result


def _points_cell(group: ScoringGroup, counts: dict[str, int]) -> str:
    if group.points is None:
        raise ScoringTableError(f"subtask {group.name} has no points")
    if group.points_policy != EACH_TEST:
        return str(group.points)
    count = counts.get(group.name)
    if not count:
        raise ScoringTableError(
            f"subtask {group.name} (each_test): its number of tests is unknown — "
            "the tests must be generated first (run_tests)"
        )
    if group.points % count:
        raise ScoringTableError(
            f"subtask {group.name} (each_test): {count} test(s) don't divide its "
            f"{group.points} points"
        )
    return f"{group.points} ({group.points // count})"


def render_scoring(
    language: str,
    groups: list[ScoringGroup],
    texts: dict[str, str | None],
    counts: dict[str, int],
) -> str:
    """`scoring.tex` in `language` (`SCORING_LANGUAGES`): the total, the paragraph on how
    the subtasks are scored, the table. `counts` — the number of tests of each group
    (needed for the `each_test` groups). `ScoringTableError` — see the class."""
    words = _WORDS.get(language)
    if words is None:
        raise ScoringTableError(
            f"the subtasks table is assembled only in {', '.join(SCORING_LANGUAGES)}, "
            f"not in '{language}'"
        )
    rows = []
    for group in groups:
        constraints = texts.get(group.name) or words.no_constraints
        last = (
            words.each_test_mark
            if group.points_policy == EACH_TEST
            else words.feedback.get(group.feedback_policy, group.feedback_policy)
        )
        dependencies = ", ".join(group.dependencies)
        rows.append(
            f"{group.name} & {_points_cell(group, counts)} & \\centering {constraints} & "
            f"{dependencies} & {last} \\\\\n\\hline"
        )
    total = sum(group.points or 0 for group in groups)
    each_test = [group for group in groups if group.points_policy == EACH_TEST]
    if not each_test:
        rule = words.complete_group
    elif len(each_test) == len(groups):
        rule = words.all_each_test
    else:
        rule = words.some_each_test
    parts = [
        words.total.format(points=total, unit=_points_unit(language, total)),
        "",
        rule,
        "",
        "\\begin{center}",
        "\\renewcommand{\\arraystretch}{1.3}",
        "\\begin{tabular}{|c|c|p{5cm}|c|c|}",
        "\\hline",
        words.header,
        "\\\\ \\hline",
        *rows,
        "\\end{tabular}",
        "\\end{center}",
    ]
    return "\n".join(parts) + "\n"
