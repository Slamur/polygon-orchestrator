from pathlib import Path

import pytest

from orchestrator import cli
from orchestrator.checks.scoring_check import check_scoring, numbers, table_rows

REPO_ROOT = Path(__file__).parent.parent
SAMPLE = REPO_ROOT / "examples" / "number" / "roots-subtasks"
PROBLEM_ID = "example-problem"

_CONSTRAINTS = """\
problem_id: example-problem
test_groups:
  - name: "1"
    variables: {N: {max: 1000}}
    points: 30
    dependencies: []
  - name: "2"
    variables: null
    special_property: "all a_i are equal"
    points: 20
    dependencies: []
  - name: "3"
    points: 50
    dependencies: ["1", "2"]
validator:
  constants:
    - name: "N"
      max: 200000
  groups:
    - name: "1"
      constants:
        - name: "N"
          max: 1000
        - name: "A"
          max: 1000000000
"""


def _table(*rows: str) -> str:
    header = (
        "\\textbf{Subtask} & \\textbf{Points} & \\parbox{5cm}{\\centering \\textbf{Constraints}} &\n"
        "\\parbox{3cm}{\\textbf{\\centering\\\\Necessary\\\\subtasks}} & Verification \\\\ \\hline\n"
    )
    body = "".join(f"{row} \\\\\n\\hline\n" for row in rows)
    return (
        "The problem is scored out of 100 points. % a comment & 7 \\\\\n\n"
        "\\begin{center}\n\\begin{tabular}{|c|c|p{5cm}|c|c|}\n\\hline\n"
        f"{header}{body}\\end{{tabular}}\n\\end{{center}}\n"
    )


_GOOD_ROWS = (
    "1 & 30 & \\centering $N \\le 10^3$, $a_i \\le 10^{9}$ & & first error",
    "2 & 20 & all $a_i$ are equal & & first error",
    "3 & 50 & No additional constraints & 1, 2 & first error",
)


def _problem(
    tmp_path: Path, scoring: dict[str, str] | None, constraints: str | None = _CONSTRAINTS
):
    problem_dir = tmp_path / "problems" / PROBLEM_ID
    for code in ("ru", "en"):
        (problem_dir / "statement" / code).mkdir(parents=True)
        (problem_dir / "statement" / code / "legend.tex").write_text("x\n", encoding="utf-8")
    for code, text in (scoring or {}).items():
        (problem_dir / "statement" / code / "scoring.tex").write_text(text, encoding="utf-8")
    if constraints is not None:
        (problem_dir / "authoring").mkdir()
        (problem_dir / "authoring" / "constraints.yaml").write_text(constraints, encoding="utf-8")
    return problem_dir


def test_sample_scoring_matches_its_groups():
    assert check_scoring(SAMPLE) == []


def test_matching_table_in_both_languages(tmp_path):
    table = _table(*_GOOD_ROWS)
    assert check_scoring(_problem(tmp_path, {"ru": table, "en": table})) == []


def test_row_of_the_examples_group_is_allowed(tmp_path):
    table = _table("0 & 0 & --- & & first error", *_GOOD_ROWS)
    assert check_scoring(_problem(tmp_path, {"ru": table, "en": table})) == []


def test_missing_in_one_language(tmp_path):
    errors = check_scoring(_problem(tmp_path, {"ru": _table(*_GOOD_ROWS)}))
    assert errors == ["statement/en/scoring.tex: missing — the problem has test groups"]


def test_mismatches_are_reported(tmp_path):
    table = _table(
        "1 & 25 & $N \\le 10^4$ & & first error",
        "3 & 50 & No additional constraints & 1 & first error",
    )
    errors = check_scoring(_problem(tmp_path, {"ru": table, "en": _table(*_GOOD_ROWS)}))
    assert errors == [
        "statement/ru/scoring.tex: subtasks 1, 3 in the table, expected 1, 2, 3",
        "statement/ru/scoring.tex: subtask 1: points 25, expected 30",
        "statement/ru/scoring.tex: subtask 1: the constraints don't mention "
        "1000, 1000000000 ('$N \\le 10^4$')",
        "statement/ru/scoring.tex: subtask 3: necessary subtasks '1', expected 1, 2",
    ]


def test_group_variable_min_is_checked(tmp_path):
    constraints = _CONSTRAINTS.replace(
        "    variables: null\n", '    variables: {K: {min: 5, max: "N"}}\n'
    )
    table = _table(*_GOOD_ROWS)
    errors = check_scoring(_problem(tmp_path, {"ru": table, "en": table}, constraints))
    assert errors == [
        f"statement/{code}/scoring.tex: subtask 2: the constraints don't mention 5 "
        "('all $a_i$ are equal')"
        for code in ("en", "ru")
    ]


def test_no_tabular(tmp_path):
    errors = check_scoring(_problem(tmp_path, {"ru": "text\n", "en": _table(*_GOOD_ROWS)}))
    assert errors == ["statement/ru/scoring.tex: no tabular with the subtasks"]


def test_without_groups(tmp_path):
    no_groups = "problem_id: example-problem\n"
    assert check_scoring(_problem(tmp_path, None, no_groups)) == []
    assert check_scoring(_problem(tmp_path / "a", {"ru": "x"}, None)) == []
    errors = check_scoring(_problem(tmp_path / "b", {"ru": "x"}, no_groups))
    assert errors == [
        "statement/ru/scoring.tex: constraints.yaml has no test_groups — "
        "a table of subtasks the problem doesn't have"
    ]


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("$N \\le 10$", {10}),
        ("$1 \\le N \\le 10^{12}$", {1, 10**12}),
        ("$N \\le 2 \\cdot 10^5$", {200000}),
        ("$N \\le 10\\,000$, $M \\le 10{,}000$", {10000}),
        ("$N \\le 1.5 \\times 10^3$", {1500}),
        ("no numbers", set()),
    ],
)
def test_numbers(cell, expected):
    assert numbers(cell) == expected


def test_table_rows_skip_header_and_escaped_ampersand():
    rows = table_rows(_table("1 & 30 & A \\& B & & first error"))
    assert rows == [["1", "30", "A \\& B", "", "first error"]]


def test_local_check_prints_scoring_errors(tmp_path, capsys):
    _problem(tmp_path, {"ru": _table(*_GOOD_ROWS)})
    rc = cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "check"])
    assert rc == 1
    assert "statement/en/scoring.tex: missing" in capsys.readouterr().out


def test_points_of_one_test_of_an_each_test_group(tmp_path):
    constraints = _CONSTRAINTS.replace(
        "    points: 30\n", "    points: 30\n    points_policy: each_test\n", 1
    )
    good = _table(_GOOD_ROWS[0].replace("& 30 &", "& 30 (2) &"), *_GOOD_ROWS[1:])
    assert check_scoring(_problem(tmp_path / "a", {"ru": good, "en": good}, constraints)) == []

    bad = _table(
        _GOOD_ROWS[0].replace("& 30 &", "& 30 (7) &"),
        _GOOD_ROWS[1].replace("& 20 &", "& 20 (4) &"),
        _GOOD_ROWS[2],
    )
    errors = check_scoring(_problem(tmp_path / "b", {"ru": bad, "en": good}, constraints))
    assert errors == [
        "statement/ru/scoring.tex: subtask 1: points of one test (7) don't divide its 30 points",
        "statement/ru/scoring.tex: subtask 2: points of one test (4) for a group scored "
        "only as a whole",
    ]
