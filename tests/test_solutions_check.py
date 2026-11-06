import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator.cache import load_cache_entry
from orchestrator.checks.solutions_run import SolutionOutcome, SolutionsRunResult
from orchestrator.checks.solutions_run import TestOutcome as Outcome
from orchestrator.llm_report import COMMENT_MARKER
from orchestrator.spec import load_spec
from orchestrator.steps import solutions_check
from orchestrator.steps.solutions import GATES, STAGES
from orchestrator.steps.solutions_check import (
    STATUS_FAILED,
    GateFailedError,
    load_run_report,
    read_author_comment,
)

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"

RUN_MAIN = GATES["run_main"]
MAIN_NAME = "ok_py_main_draft.py"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / PROBLEM_ID
    (directory / "solutions").mkdir(parents=True)
    (directory / "solutions" / MAIN_NAME).write_text("print(1)\n", encoding="utf-8")
    return directory


def _run_gate(gate, spec, problem_dir):
    upstream = {
        gate.INPUTS_KEY: gate.load_inputs(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
    }
    return gate.run_step(
        PROBLEM_ID, spec, upstream, problems_dir=problem_dir.parent, templates_dir=TEMPLATES_DIR
    )


def _result_with(*outcomes, errors=()):
    return SolutionsRunResult(solutions=list(outcomes), errors=list(errors), full=True)


def _failing_main():
    return SolutionOutcome(
        MAIN_NAME,
        "ok",
        frozenset({"OK"}),
        tests=[Outcome("test 2", "RE", 0.1, "exit code 1", "gen_rand -n=5", generated=True)],
    )


def _stub_run(monkeypatch, result):
    calls = []

    def run(*args, only=None, **kwargs):
        calls.append(set(only))
        return result

    monkeypatch.setattr(solutions_check, "run_solutions", run)
    return calls


def test_passing_run_records_a_confirmed_entry(spec, problem_dir, monkeypatch):
    outcome = SolutionOutcome(MAIN_NAME, "ok", frozenset({"OK"}), tests=[Outcome("t", "OK")])
    calls = _stub_run(monkeypatch, _result_with(outcome))

    result = _run_gate(RUN_MAIN, spec, problem_dir)

    assert calls == [{MAIN_NAME}]
    assert result.status == "confirmed"
    assert [n["kind"] for n in result.notes] == ["passed"]
    entry = load_cache_entry(PROBLEM_ID, "run_main", problems_dir=problem_dir.parent)
    assert entry.status == "confirmed"


def test_failed_run_raises_and_its_report_stays_current_until_an_input_changes(
    spec, problem_dir, monkeypatch
):
    _stub_run(monkeypatch, _result_with(_failing_main()))

    with pytest.raises(GateFailedError) as exc_info:
        _run_gate(RUN_MAIN, spec, problem_dir)

    assert "1 solution(s) failed" in exc_info.value.detail
    failed = [n for n in exc_info.value.notes if n["kind"] == "failed"]
    assert failed[0]["field"] == MAIN_NAME
    assert "test 2: RE (gen_rand -n=5) — exit code 1" in failed[0]["explanation"]
    entry = load_cache_entry(PROBLEM_ID, "run_main", problems_dir=problem_dir.parent)
    assert entry.status == STATUS_FAILED
    report = load_run_report(PROBLEM_ID, "run_main", problems_dir=problem_dir.parent)
    assert [f["name"] for f in report["failures"]] == [MAIN_NAME]

    current = RUN_MAIN.failed_report(PROBLEM_ID, spec, [MAIN_NAME], problems_dir=problem_dir.parent)
    assert current["failures"][0]["unexpected"][0]["verdict"] == "RE"
    assert current["report"] == "llm_reports/run_main.md"
    assert (
        RUN_MAIN.failed_report(PROBLEM_ID, spec, ["other.py"], problems_dir=problem_dir.parent)
        is None
    )

    # A test-script edit is an input of the run too: the report is outdated.
    (problem_dir / "test_script").write_text("gen_rand -n=6 > $\n", encoding="utf-8")
    assert (
        RUN_MAIN.failed_report(PROBLEM_ID, spec, [MAIN_NAME], problems_dir=problem_dir.parent)
        is None
    )


def test_problem_errors_fail_the_run_but_are_not_given_to_the_stage(spec, problem_dir, monkeypatch):
    outcome = SolutionOutcome(MAIN_NAME, "ok", frozenset({"OK"}), tests=[Outcome("t", "OK")])
    _stub_run(monkeypatch, _result_with(outcome, errors=["validator.cpp rejects test 3: bad"]))

    with pytest.raises(GateFailedError) as exc_info:
        _run_gate(RUN_MAIN, spec, problem_dir)

    assert [n["kind"] for n in exc_info.value.notes] == ["error"]
    assert (
        RUN_MAIN.failed_report(PROBLEM_ID, spec, [MAIN_NAME], problems_dir=problem_dir.parent)
        is None
    )


def test_survivor_is_reported_but_does_not_fail_the_run(spec, problem_dir, monkeypatch):
    wrong = GATES["run_wrong"]
    (problem_dir / "solutions" / "wa_py_off_draft.py").write_text("print(2)\n", encoding="utf-8")
    survivor = SolutionOutcome(
        "wa_py_off_draft.py",
        "wa",
        frozenset({"OK", "WA"}),
        tests=[Outcome("test 2", "OK", generated=True)],
        required=frozenset({"WA"}),
    )
    calls = _stub_run(monkeypatch, _result_with(survivor))

    result = _run_gate(wrong, spec, problem_dir)

    assert calls == [{"wa_py_off_draft.py"}]
    assert [n["kind"] for n in result.notes] == ["passed", "survived"]


def test_ok_run_judges_the_main_solution_too(problem_dir):
    (problem_dir / "solutions" / "ok_py_alt_draft.py").write_text("print(1)\n", encoding="utf-8")
    assert sorted(GATES["run_ok"].judged(problem_dir)) == ["ok_py_alt_draft.py", MAIN_NAME]
    assert GATES["run_main"].judged(problem_dir) == [MAIN_NAME]


def test_author_comment_is_read_after_the_marker(problem_dir):
    assert read_author_comment(problem_dir, "run_main") is None
    report = problem_dir / "llm_reports" / "run_main.md"
    report.parent.mkdir()
    report.write_text(f"# run_main\n\n{COMMENT_MARKER}\n", encoding="utf-8")
    assert read_author_comment(problem_dir, "run_main") is None
    report.write_text(f"# run_main\n\n{COMMENT_MARKER}\n\nthe sample is wrong\n", encoding="utf-8")
    assert read_author_comment(problem_dir, "run_main") == "the sample is wrong"


def test_stage_reads_the_failed_report_of_its_run(spec, problem_dir, monkeypatch):
    _stub_run(monkeypatch, _result_with(_failing_main()))
    with pytest.raises(GateFailedError):
        _run_gate(RUN_MAIN, spec, problem_dir)

    inputs = STAGES["main"].load_inputs(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
    assert [f["name"] for f in inputs["local_run"]["failures"]] == [MAIN_NAME]

    (problem_dir / "solutions" / MAIN_NAME).write_text("print(2)\n", encoding="utf-8")
    inputs = STAGES["main"].load_inputs(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
    assert inputs["local_run"] is None


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("java") is None, reason="java not found")
def test_real_run_fails_on_a_wrong_main_solution(real_local_runs, spec, problem_dir):
    # A synthetic sum of two numbers: the main solution prints the difference.
    examples = problem_dir / "statement" / "examples"
    examples.mkdir(parents=True)
    (examples / "example_1.txt").write_text("1 2\n", encoding="utf-8")
    (problem_dir / "authoring").mkdir()
    (problem_dir / "authoring" / "spec.yaml").write_text(
        yaml.safe_dump(
            {
                "problem_id": PROBLEM_ID,
                "statement_draft": {"sample_examples": [{"input": "1 2\n", "output": "3\n"}]},
                "checker": {"standard": "ncmp", "custom_needed": False},
            }
        ),
        encoding="utf-8",
    )
    (problem_dir / "limits.yaml").write_text(
        "time_limit_seconds: 1\nmemory_limit_mb: 256\n", encoding="utf-8"
    )
    (problem_dir / "solutions" / MAIN_NAME).write_text(
        "a, b = map(int, input().split())\nprint(a - b)\n", encoding="utf-8"
    )

    with pytest.raises(GateFailedError):
        _run_gate(RUN_MAIN, spec, problem_dir)

    report = load_run_report(PROBLEM_ID, "run_main", problems_dir=problem_dir.parent)
    assert [f["name"] for f in report["failures"]] == [MAIN_NAME]
    assert report["failures"][0]["unexpected"][0]["verdict"] == "WA"
