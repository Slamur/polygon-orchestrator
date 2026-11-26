from pathlib import Path

import pytest

from orchestrator.cache import load_cache_entry
from orchestrator.checks.local_tests import (
    ISSUE_EQUAL_EXAMPLES,
    ISSUE_EQUAL_TESTS,
    ISSUE_GENERATOR_BUILD,
    ISSUE_GENERATOR_MISSING,
    ISSUE_GENERATOR_RUN,
    ISSUE_SCRIPT,
    ISSUE_VALIDATOR_BUILD,
    ISSUE_VALIDATOR_REJECT,
)
from orchestrator.checks.local_tests import TestIssue as Issue
from orchestrator.llm_report import COMMENT_MARKER
from orchestrator.spec import load_spec
from orchestrator.steps import tests_check
from orchestrator.steps.solutions_check import STATUS_FAILED, GateFailedError
from orchestrator.steps.tests_check import REVISING_STEPS, TESTS_GATE

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / PROBLEM_ID
    (directory / "generators").mkdir(parents=True)
    (directory / "generators" / "gen_rand.cpp").write_text("int main() {}\n", encoding="utf-8")
    (directory / "test_script").write_text("gen_rand -n=5 > $\n", encoding="utf-8")
    (directory / "validator.cpp").write_text("int main() {}\n", encoding="utf-8")
    (directory / "authoring").mkdir()
    (directory / "authoring" / "tests_design.yaml").write_text("ideas: []\n", encoding="utf-8")
    return directory


def _stub(monkeypatch, *issues, warnings=()):
    monkeypatch.setattr(
        tests_check, "_run", lambda *args, **kwargs: (list(issues), list(warnings), {None: 3})
    )


def _run_gate(spec, problem_dir):
    upstream = {
        TESTS_GATE.INPUTS_KEY: TESTS_GATE.load_inputs(
            PROBLEM_ID, spec, problems_dir=problem_dir.parent
        )
    }
    return TESTS_GATE.run_step(
        PROBLEM_ID, spec, upstream, problems_dir=problem_dir.parent, templates_dir=TEMPLATES_DIR
    )


def _routed(problem_dir):
    return {
        step: [
            issue["message"]
            for issue in (
                TESTS_GATE.failed_report(PROBLEM_ID, step, problems_dir=problem_dir.parent)
                or {"issues": []}
            )["issues"]
        ]
        for step in REVISING_STEPS
    }


def test_passing_run_records_a_confirmed_entry(spec, problem_dir, monkeypatch):
    _stub(monkeypatch, warnings=["test 2 and test 3 are equal"])

    result = _run_gate(spec, problem_dir)

    assert result.status == "confirmed"
    assert [note["kind"] for note in result.notes] == ["passed", "warning"]
    entry = load_cache_entry(PROBLEM_ID, "run_tests", problems_dir=problem_dir.parent)
    assert entry.status == "confirmed"
    assert _routed(problem_dir) == {step: [] for step in REVISING_STEPS}


def test_report_keeps_the_tests_per_group_of_a_passed_run_only(spec, problem_dir, monkeypatch):
    from orchestrator.steps.solutions_check import load_run_report

    counts = {"1": 10, "2": 5, None: 1}
    monkeypatch.setattr(tests_check, "_run", lambda *args, **kwargs: ([], [], counts))
    result = _run_gate(spec, problem_dir)
    assert "16 test(s) generated" in result.notes[0]["explanation"]
    report = load_run_report(PROBLEM_ID, "run_tests", problems_dir=problem_dir.parent)
    assert report["group_counts"] == {"1": 10, "2": 5}

    issue = Issue(ISSUE_SCRIPT, "broken", "gen_rand > $", 1)
    monkeypatch.setattr(tests_check, "_run", lambda *args, **kwargs: ([issue], [], counts))
    with pytest.raises(GateFailedError):
        _run_gate(spec, problem_dir)
    report = load_run_report(PROBLEM_ID, "run_tests", problems_dir=problem_dir.parent)
    assert "group_counts" not in report


@pytest.mark.parametrize(
    ("kind", "steps"),
    [
        (ISSUE_SCRIPT, {"test_script_draft"}),
        (ISSUE_GENERATOR_BUILD, {"generators_draft"}),
        (ISSUE_GENERATOR_MISSING, {"generators_draft", "test_script_draft"}),
        (ISSUE_GENERATOR_RUN, set(REVISING_STEPS)),
        (ISSUE_VALIDATOR_REJECT, set(REVISING_STEPS)),
        (ISSUE_VALIDATOR_BUILD, set()),
        (ISSUE_EQUAL_TESTS, set(REVISING_STEPS)),
        (ISSUE_EQUAL_EXAMPLES, set()),
    ],
)
def test_failed_run_routes_each_error_to_the_steps_that_may_cause_it(
    spec, problem_dir, monkeypatch, kind, steps
):
    _stub(monkeypatch, Issue(kind, "test 2: broken", "gen_rand -n=5 > $", 1, "gen_rand"))

    with pytest.raises(GateFailedError) as failure:
        _run_gate(spec, problem_dir)

    entry = load_cache_entry(PROBLEM_ID, "run_tests", problems_dir=problem_dir.parent)
    assert entry.status == STATUS_FAILED
    routed = _routed(problem_dir)
    assert {step for step, messages in routed.items() if messages} == steps
    assert all(messages in ([], ["test 2: broken"]) for messages in routed.values())
    if steps:
        assert "revise with the report" in failure.value.detail
    else:
        assert "fix them by hand" in failure.value.detail


def test_report_is_outdated_for_a_step_once_its_files_change(spec, problem_dir, monkeypatch):
    _stub(monkeypatch, Issue(ISSUE_GENERATOR_RUN, "test 2: exit code 3"))
    with pytest.raises(GateFailedError):
        _run_gate(spec, problem_dir)

    (problem_dir / "generators" / "gen_rand.cpp").write_text("int main(){ }\n", encoding="utf-8")

    routed = _routed(problem_dir)
    assert routed["generators_draft"] == []
    assert routed["tests_plan"] == routed["test_script_draft"] == ["test 2: exit code 3"]


def test_report_is_outdated_for_every_step_once_the_validator_changes(
    spec, problem_dir, monkeypatch
):
    _stub(monkeypatch, Issue(ISSUE_VALIDATOR_REJECT, "validator.cpp rejects test 2"))
    with pytest.raises(GateFailedError):
        _run_gate(spec, problem_dir)

    (problem_dir / "validator.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    assert _routed(problem_dir) == {step: [] for step in REVISING_STEPS}


def test_report_carries_the_authors_comment(spec, problem_dir, monkeypatch):
    _stub(monkeypatch, Issue(ISSUE_SCRIPT, "test_script: no '> $' redirect"))
    with pytest.raises(GateFailedError):
        _run_gate(spec, problem_dir)
    report = problem_dir / "llm_reports" / "run_tests.md"
    report.parent.mkdir()
    report.write_text(f"# run_tests\n\n{COMMENT_MARKER}\nkeep the order\n", encoding="utf-8")

    found = TESTS_GATE.failed_report(
        PROBLEM_ID, "test_script_draft", problems_dir=problem_dir.parent
    )

    assert found["comment"] == "keep the order"
    assert found["report"] == "llm_reports/run_tests.md"
    assert "revising with its report" in TESTS_GATE.revision_reason(found)
