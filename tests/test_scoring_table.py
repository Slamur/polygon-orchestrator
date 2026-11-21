import json
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import pipeline
from orchestrator.checks.scoring_check import check_scoring
from orchestrator.scoring_table import (
    ScoringGroup,
    ScoringTableError,
    parse_scoring_texts,
    render_scoring,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

STEP = "scoring_table"


def _group(name, points, policy="complete_group", deps=(), feedback="icpc"):
    return ScoringGroup(name, points, tuple(deps), feedback, policy)


# --- the renderer -----------------------------------------------------------------


def test_complete_group_table():
    groups = [_group("1", 40), _group("2", 60, deps=["1"], feedback="points")]
    table = render_scoring("en", groups, {"1": "$N \\le 10$", "2": None}, {})
    assert table.startswith("The problem is scored out of 100 points")
    assert "A subtask is tested if all tests of the necessary subtasks are passed." in table
    assert (
        "\\begin{itemize}\n"
        "\\item ``first error''~--- the subtask's points are awarded only if all tests of "
        "this subtask are passed;\n"
        "\\item ``points only''~--- the subtask's points are awarded only if all tests of "
        "this subtask are passed.\n"
        "\\end{itemize}"
    ) in table
    assert "1 & 40 & \\centering $N \\le 10$ &  & first error \\\\\n\\hline" in table
    assert "2 & 60 & \\centering No additional constraints & 1 & points only \\\\" in table
    assert "points per test" not in table


def test_each_test_points_come_with_the_points_of_one_test():
    groups = [_group("1", 30, "each_test"), _group("2", 70, "each_test", deps=["1"])]
    table = render_scoring("ru", groups, {"1": "$N \\le 10$", "2": None}, {"1": 15, "2": 7})
    assert "Задача оценивается в 100 баллов" in table
    assert "Подзадача проверяется, если пройдены все тесты необходимых подзадач." in table
    assert table.count("\\item") == 1
    assert "\\item <<баллы за каждый тест>>~--- каждый тест подзадачи оценивается" in table
    assert "1 & 30 (2) & \\centering $N \\le 10$ &  & баллы за каждый тест \\\\" in table
    assert (
        "2 & 70 (10) & \\centering Без дополнительных ограничений & 1 & баллы за каждый тест"
        in (table)
    )
    assert "первая ошибка" not in table


def test_mixed_policies_explain_the_each_test_subtasks():
    groups = [_group("1", 20, "each_test"), _group("2", 80)]
    table = render_scoring("en", groups, {"1": None, "2": None}, {"1": 4})
    assert "\\item ``points per test''~--- each test of the subtask is scored" in table
    assert "\\item ``first error''~--- the subtask's points are awarded only if" in table
    assert table.index("``points per test''~---") < table.index("``first error''~---")
    assert "1 & 20 (5) &" in table
    assert "2 & 80 &" in table and "& first error \\\\" in table


@pytest.mark.parametrize(
    ("total", "unit"), [(1, "балл"), (21, "балл"), (3, "балла"), (11, "баллов"), (100, "баллов")]
)
def test_russian_plural_of_the_total(total, unit):
    table = render_scoring("ru", [_group("1", total)], {"1": None}, {})
    assert f"Задача оценивается в {total} {unit} " in table


@pytest.mark.parametrize(
    ("counts", "message"),
    [({}, "number of tests is unknown"), ({"1": 7}, "don't divide its 30 points")],
)
def test_each_test_needs_a_dividing_number_of_tests(counts, message):
    with pytest.raises(ScoringTableError, match=message):
        render_scoring("en", [_group("1", 30, "each_test")], {"1": None}, counts)


def test_only_the_generation_languages():
    with pytest.raises(ScoringTableError, match="not in 'kazakh'"):
        render_scoring("kazakh", [_group("1", 100)], {"1": None}, {})


def test_parse_scoring_texts():
    text = 'constraints:\n  "1": "$N \\\\le 10$"\n  2: null\n  "3": "  "\n'
    assert parse_scoring_texts(text, ["1", "2", "3"]) == {"1": "$N \\le 10$", "2": None, "3": None}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("constraints: [1]\n", "expected a mapping"),
        ('constraints:\n  "1": null\n', "no constraints of subtask\\(s\\) 2"),
        ('constraints:\n  "1": null\n  "2": null\n  "9": null\n', "9 aren't test groups"),
        ('constraints:\n  "1": 5\n  "2": null\n', "must be a string or null"),
    ],
)
def test_parse_scoring_texts_rejects(text, message):
    with pytest.raises(ScoringTableError, match=message):
        parse_scoring_texts(text, ["1", "2"])


# --- the step ---------------------------------------------------------------------

_CONSTRAINTS = """\
n_max: 100000
limits:
  time_limit_seconds: 1
  memory_limit_mb: 256
test_groups:
  - name: "1"
    variables: {N: {max: 10}}
    points: 30
    points_policy: each_test
    dependencies: []
  - name: "2"
    points: 70
    dependencies: ["1"]
"""
_TEXTS = 'constraints:\n  "1": "$N \\\\le 10$"\n  "2": null\n'


@pytest.fixture
def problems_dir(tmp_path):
    authoring = tmp_path / "problems" / "p1" / "authoring"
    authoring.mkdir(parents=True)
    spec = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    (authoring / "spec.yaml").write_text(
        spec.replace("problem_id: valid-spec", "problem_id: p1"), encoding="utf-8"
    )
    (authoring / "constraints.yaml").write_text(_CONSTRAINTS, encoding="utf-8")
    (authoring / "scoring.yaml").write_text(_TEXTS, encoding="utf-8")
    _record_counts(tmp_path / "problems", {"1": 15, "2": 4})
    return tmp_path / "problems"


def _record_counts(problems_dir: Path, counts: dict[str, int]) -> None:
    report = problems_dir / "p1" / ".cache" / "run_tests.report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"issues": [], "group_counts": counts}), encoding="utf-8")


def _table_path(problems_dir: Path) -> Path:
    return problems_dir / "p1" / "statement" / "ru" / "scoring.tex"


def _run(problems_dir: Path) -> pipeline.StepOutcome:
    with patch("orchestrator.model_router.call_model") as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step=STEP,
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )
    mock_call.assert_not_called()
    [outcome] = result.outcomes
    return outcome


def test_step_assembles_the_table_and_reruns_only_on_a_changed_input(problems_dir):
    outcome = _run(problems_dir)
    assert outcome.status == pipeline.OUTCOME_CONFIRMED
    table = _table_path(problems_dir).read_text(encoding="utf-8")
    assert "1 & 30 (2) & \\centering $N \\le 10$ &  & баллы за каждый тест \\\\" in table
    assert "2 & 70 & \\centering Без дополнительных ограничений & 1 & первая ошибка" in table
    assert check_scoring(problems_dir / "p1") == []

    assert _run(problems_dir).status == pipeline.OUTCOME_CACHE_HIT

    _record_counts(problems_dir, {"1": 10, "2": 4})
    assert _run(problems_dir).status == pipeline.OUTCOME_CONFIRMED
    assert "1 & 30 (3) &" in _table_path(problems_dir).read_text(encoding="utf-8")


def test_a_hand_edited_table_is_backed_up_when_replaced(problems_dir):
    _run(problems_dir)
    _table_path(problems_dir).write_text("edited\n", encoding="utf-8")
    (problems_dir / "p1" / "authoring" / "scoring.yaml").write_text(
        _TEXTS.replace("null", '"$N \\\\le 100$"'), encoding="utf-8"
    )
    outcome = _run(problems_dir)
    assert outcome.status == pipeline.OUTCOME_CONFIRMED
    assert outcome.backup_dir is not None
    assert (outcome.backup_dir / "statement" / "ru" / "scoring.tex").read_text() == "edited\n"
    assert "$N \\le 100$" in _table_path(problems_dir).read_text(encoding="utf-8")


def test_step_without_scoring_texts_is_an_error(problems_dir):
    (problems_dir / "p1" / "authoring" / "scoring.yaml").unlink()
    outcome = _run(problems_dir)
    assert outcome.status == pipeline.OUTCOME_ERROR
    assert "no authoring/scoring.yaml" in outcome.detail
    assert not _table_path(problems_dir).exists()


def test_step_is_skipped_without_groups_in_constraints(problems_dir):
    constraints = problems_dir / "p1" / "authoring" / "constraints.yaml"
    constraints.write_text(_CONSTRAINTS.split("test_groups:")[0], encoding="utf-8")
    outcome = _run(problems_dir)
    assert outcome.status == pipeline.OUTCOME_SKIPPED_OPTIONAL
    assert "constraints.yaml has no test groups" in outcome.detail
