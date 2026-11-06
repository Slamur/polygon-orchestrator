import copy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.checks.solutions_run import SolutionOutcome, SolutionsRunResult
from orchestrator.checks.solutions_run import TestOutcome as Outcome
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps import solutions_check, tests_survivors
from orchestrator.steps.solutions import GATES
from orchestrator.steps.tests_plan import TestsPlanError, check_plan_extends

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")
PROBLEM_ID = "example-problem"
CONSTRAINTS = {"constraints.yaml": "n_max: 100000\n"}

MAIN = "ok_cpp_main_draft.cpp"
WRONG = "wa_cpp_off_draft.cpp"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / PROBLEM_ID
    (directory / "solutions").mkdir(parents=True)
    (directory / "solutions" / MAIN).write_text("int main() { /* main */ }\n", encoding="utf-8")
    (directory / "solutions" / WRONG).write_text("int main() { /* off */ }\n", encoding="utf-8")
    (directory / "authoring").mkdir()
    (directory / "authoring" / "tests_plan.yaml").write_text(PLAN_YAML, encoding="utf-8")
    (directory / "limits.yaml").write_text(
        "time_limit_seconds: 2\nmemory_limit_mb: 256\n", encoding="utf-8"
    )
    return directory


def _plan() -> dict:
    return yaml.safe_load(PLAN_YAML)


def _extended_plan() -> dict:
    plan = _plan()
    plan["tests"].append(
        {
            "description": "The right bound",
            "status": "proposed",
            "rationale": "the off-by-one solution misses the last element",
            "covers": [f"solutions/{WRONG}"],
            "commands": ["gen_rand -n=1"],
            "repeat": None,
        }
    )
    return plan


# --- check_plan_extends -----------------------------------------------------------


def test_extending_the_plan_is_accepted():
    check_plan_extends(_plan(), _extended_plan())


@pytest.mark.parametrize(
    "change",
    [
        lambda plan: plan["tests"].pop(0),
        lambda plan: plan["tests"][0]["commands"].append("gen_rand -n=2"),
        lambda plan: plan["generators"][0].update(algorithm="another"),
        lambda plan: plan["constants"].clear(),
    ],
)
def test_changing_the_current_plan_is_rejected(change):
    new = copy.deepcopy(_extended_plan())
    change(new)
    with pytest.raises(TestsPlanError, match="keep the current plan"):
        check_plan_extends(_plan(), new)


# --- survivors ----------------------------------------------------------------


def _run_gates(spec, problem_dir, monkeypatch, *, caught: bool):
    """`run_slow` and `run_wrong` over a stub run: the wrong solution survives or is caught."""
    verdict = "WA" if caught else "OK"
    wrong = SolutionOutcome(
        WRONG,
        "wa",
        frozenset({"OK", "WA"}),
        tests=[Outcome("test 1", verdict, 0.5, generated=True)],
        required=frozenset({"WA"}),
    )
    result = SolutionsRunResult(full=True, solutions=[wrong])
    monkeypatch.setattr(solutions_check, "run_solutions", lambda *a, **kw: result)
    for name in tests_survivors.SURVIVOR_RUNS:
        gate = GATES[name]
        upstream = {
            gate.INPUTS_KEY: gate.load_inputs(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
        }
        gate.run_step(PROBLEM_ID, spec, upstream, problems_dir=problem_dir.parent)


def test_skipped_while_every_wrong_solution_is_caught(spec, problem_dir, monkeypatch):
    kwargs = {"problem_id": PROBLEM_ID, "problems_dir": problem_dir.parent}
    assert tests_survivors.skip_detail(spec, **kwargs) is not None  # no run yet
    _run_gates(spec, problem_dir, monkeypatch, caught=True)
    assert tests_survivors.skip_detail(spec, **kwargs) is not None
    _run_gates(spec, problem_dir, monkeypatch, caught=False)
    assert tests_survivors.skip_detail(spec, **kwargs) is None


def test_survivors_are_outdated_when_their_solution_changes(spec, problem_dir, monkeypatch):
    _run_gates(spec, problem_dir, monkeypatch, caught=False)
    assert [
        s["name"]
        for s in tests_survivors.survivors(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
    ] == [WRONG]
    (problem_dir / "solutions" / WRONG).write_text("int main() { /* fixed */ }\n", encoding="utf-8")
    assert tests_survivors.survivors(PROBLEM_ID, spec, problems_dir=problem_dir.parent) == []


def _upstream(spec, problem_dir):
    inputs = tests_survivors.load_inputs(PROBLEM_ID, spec, problems_dir=problem_dir.parent)
    return {**CONSTRAINTS, tests_survivors.INPUTS_KEY: inputs}


def _run(spec, problem_dir, plan):
    response = ModelResponse(status="proposed", artifacts={"tests_plan.yaml": yaml.safe_dump(plan)})
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = tests_survivors.run_step(
            PROBLEM_ID,
            spec,
            _upstream(spec, problem_dir),
            prompts_dir=PROMPTS_DIR,
            problems_dir=problem_dir.parent,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def test_extends_the_plan_with_tests_for_the_survivors(spec, problem_dir, monkeypatch):
    _run_gates(spec, problem_dir, monkeypatch, caught=False)

    result, mock_call = _run(spec, problem_dir, _extended_plan())

    written = yaml.safe_load((problem_dir / "authoring" / "tests_plan.yaml").read_text())
    assert written["tests"][-1]["covers"] == [f"solutions/{WRONG}"]
    assert not [n for n in result.notes if n["kind"] == "uncovered"]
    prompt = mock_call.call_args.args[2]
    assert "/* off */" in prompt and "/* main */" in prompt
    assert "Random maximal tests" in prompt
    assert "declared verdict: wa; the longest local time on the current tests: 0.5s" in prompt
    assert "The problem's TL (limits.yaml): 2.0s." in prompt


def test_plan_that_drops_current_tests_is_not_written(spec, problem_dir, monkeypatch):
    _run_gates(spec, problem_dir, monkeypatch, caught=False)
    plan = _extended_plan()
    plan["tests"].pop(0)

    result, _ = _run(spec, problem_dir, plan)

    assert "keep the current plan" in result.not_applied_reason
    assert (problem_dir / "authoring" / "tests_plan.yaml").read_text() == PLAN_YAML


def test_survivor_without_a_test_gets_an_uncovered_note(spec, problem_dir, monkeypatch):
    _run_gates(spec, problem_dir, monkeypatch, caught=False)

    result, _ = _run(spec, problem_dir, _plan())

    assert [n["field"] for n in result.notes if n["kind"] == "uncovered"] == [f"solutions/{WRONG}"]


def test_hash_ignores_the_plan_and_the_times(spec, problem_dir, monkeypatch):
    _run_gates(spec, problem_dir, monkeypatch, caught=False)
    before = tests_survivors.compute_input_hash(PROBLEM_ID, spec, _upstream(spec, problem_dir))

    (problem_dir / "authoring" / "tests_plan.yaml").write_text(
        yaml.safe_dump(_extended_plan()), encoding="utf-8"
    )
    upstream = _upstream(spec, problem_dir)
    upstream[tests_survivors.INPUTS_KEY]["survivors"][0]["max_seconds"] = 9.0

    assert tests_survivors.compute_input_hash(PROBLEM_ID, spec, upstream) == before
