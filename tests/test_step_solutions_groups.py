"""`solutions_groups` — the solution stage that looks for solutions specific to particular
test groups (`orchestrator/steps/solutions.py`, `Stage("groups")`), with a mocked model."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.solutions import GATES, STAGES
from orchestrator.steps.solutions_check import GROUP_RESULTS_KEY, _group_results

GROUPS = STAGES["groups"]
REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

MAIN_CODE = {"ma_cpp_sum_draft.cpp": "int main() { /* the main one */ }"}
CONSTRAINTS = (
    "variables:\n  n: {min: 1, max: 200000}\n"
    "test_groups:\n"
    "  - name: '1'\n    variables:\n      n: {max: 15}\n    points: 30\n"
    "  - name: '2'\n    special_property: all values are equal\n    points: 30\n"
    "  - name: '3'\n    points: 40\n"
)
RESULTS = {"tl_cpp_brute_draft.cpp": {"1": "OK", "2": "TL", "3": "TL"}}


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _upstream(constraints=CONSTRAINTS, results=None, local_run=None):
    inputs = {
        "references": MAIN_CODE,
        "local_run": local_run,
        "group_results": RESULTS if results is None else results,
    }
    return {"constraints.yaml": constraints, GROUPS.INPUTS_KEY: inputs}


def _run(spec, tmp_path, response, upstream=None):
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = GROUPS.run_step(
            "my-task",
            spec,
            upstream or _upstream(),
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    return result, mock_call


def test_prompt_lists_only_groups_with_limits_of_their_own_and_the_results(spec, tmp_path):
    _, mock_call = _run(spec, tmp_path, ModelResponse(status="confirmed", artifacts={}, notes=[]))
    step, _, user_prompt = mock_call.call_args.args[:3]
    assert step == "solutions_groups"
    groups_section = user_prompt.split("## Groups to consider", 1)[1].split("\n## ", 1)[0]
    assert "### Group 1\nBounds of its own: n <= 15" in groups_section
    assert "### Group 2\nSpecial property: all values are equal" in groups_section
    assert "Group 3" not in groups_section
    assert "- tl_cpp_brute_draft.cpp: 1 OK, 2 TL, 3 TL" in user_prompt
    assert "int main() { /* the main one */ }" in user_prompt


def test_a_solution_for_a_group_is_written(spec, tmp_path):
    code = "// SUGGESTION: equal values\n// passes groups: [2]\nint main() {}\n"
    response = ModelResponse(
        status="proposed",
        artifacts={"wa_cpp_equal_draft.cpp": code},
        notes=[{"field": "test_groups[2]", "kind": "suggestion", "explanation": "found"}],
    )
    _run(spec, tmp_path, response)
    path = tmp_path / "my-task" / "solutions" / "wa_cpp_equal_draft.cpp"
    assert path.read_text(encoding="utf-8") == code
    assert list(GROUPS.owned(tmp_path / "my-task")) == ["wa_cpp_equal_draft.cpp"]
    assert not STAGES["wrong"].owned(tmp_path / "my-task")


@pytest.mark.parametrize(
    ("name", "code"),
    [
        # No comment naming the groups.
        ("wa_cpp_equal_draft.cpp", "int main() {}\n"),
        # A verdict a group solution can't have.
        ("ok_cpp_equal_draft.cpp", "// passes groups: [2]\nint main() {}\n"),
        ("tl_ok_cpp_equal_draft.cpp", "// passes groups: [2]\nint main() {}\n"),
    ],
)
def test_a_response_that_is_not_a_group_solution_is_not_applied(spec, tmp_path, name, code):
    response = ModelResponse(status="proposed", artifacts={name: code}, notes=[])
    result, _ = _run(spec, tmp_path, response)
    assert not (tmp_path / "my-task" / "solutions" / name).exists()
    assert result.conflicts or result.status != "proposed"


def test_wrong_stage_may_not_write_a_group_solution(spec, tmp_path):
    response = ModelResponse(
        status="proposed",
        artifacts={"wa_cpp_equal_draft.cpp": "// passes groups: [2]\nint main() {}\n"},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        STAGES["wrong"].run_step(
            "my-task",
            spec,
            {
                "constraints.yaml": CONSTRAINTS,
                STAGES["wrong"].INPUTS_KEY: {"references": MAIN_CODE, "local_run": None},
            },
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    assert not (tmp_path / "my-task" / "solutions" / "wa_cpp_equal_draft.cpp").exists()


def test_the_group_results_are_part_of_the_input(spec):
    first = GROUPS.compute_input_hash("my-task", spec, _upstream())
    same = GROUPS.compute_input_hash("my-task", spec, _upstream(results=dict(RESULTS)))
    other = GROUPS.compute_input_hash(
        "my-task", spec, _upstream(results={"tl_cpp_brute_draft.cpp": {"1": "OK"}})
    )
    assert first == same != other


def _write_constraints(problem_dir: Path, text: str) -> None:
    path = problem_dir / "authoring" / "constraints.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_skipped_without_groups_with_limits_of_their_own(spec, tmp_path):
    problem_dir = tmp_path / "my-task"
    _write_constraints(problem_dir, "variables:\n  n: {max: 10}\n")
    assert "no test groups" in GROUPS.skip_detail(spec, problem_id="my-task", problems_dir=tmp_path)
    _write_constraints(
        problem_dir, "variables:\n  n: {max: 10}\ntest_groups:\n  - name: '1'\n    points: 100\n"
    )
    assert "limits of its own" in GROUPS.skip_detail(
        spec, problem_id="my-task", problems_dir=tmp_path
    )
    assert "limits of its own" in GATES["run_groups"].skip_detail(
        spec, problem_id="my-task", problems_dir=tmp_path
    )
    _write_constraints(problem_dir, CONSTRAINTS)
    assert GROUPS.skip_detail(spec, problem_id="my-task", problems_dir=tmp_path) is None


def test_group_results_of_a_run_skip_the_examples():
    outcome = {
        "name": "tl_cpp_brute_draft.cpp",
        "groups": [
            {"name": "0", "passed": True, "failed_on": None},
            {"name": "1", "passed": True, "failed_on": None},
            {"name": "2", "passed": False, "failed_on": {"test": "test 9", "verdict": "TL"}},
            {"name": "3", "passed": False, "failed_on": None},
        ],
    }
    assert _group_results([outcome, {"name": "x.cpp", "groups": None}]) == {
        "tl_cpp_brute_draft.cpp": {"1": "OK", "2": "TL", "3": "not run"}
    }


def test_load_inputs_takes_the_results_of_the_current_earlier_runs(spec, tmp_path, monkeypatch):
    problem_dir = tmp_path / "my-task"
    solutions = problem_dir / "solutions"
    solutions.mkdir(parents=True)
    (solutions / "ma_cpp_sum_draft.cpp").write_text("int main() {}\n", encoding="utf-8")
    (solutions / "wa_cpp_equal_draft.cpp").write_text(
        "// passes groups: [2]\nint main() {}\n", encoding="utf-8"
    )
    cache = problem_dir / ".cache"
    cache.mkdir()
    reports = {
        "run_slow": {"tl_cpp_brute_draft.cpp": {"1": "OK", "2": "TL"}},
        # The stage's own solution isn't its own input.
        "run_groups": {"wa_cpp_equal_draft.cpp": {"1": "WA", "2": "OK"}},
    }
    for run, results in reports.items():
        (cache / f"{run}.report.json").write_text(
            json.dumps({GROUP_RESULTS_KEY: results}), encoding="utf-8"
        )
    # Every run's input is current.
    for gate in GATES.values():
        monkeypatch.setattr(
            type(gate),
            "current_group_results",
            lambda self, problem_id, spec, problems_dir: (
                json.loads(
                    (
                        Path(problems_dir) / problem_id / ".cache" / f"{self.step_name}.report.json"
                    ).read_text(encoding="utf-8")
                )[GROUP_RESULTS_KEY]
                if (
                    Path(problems_dir) / problem_id / ".cache" / f"{self.step_name}.report.json"
                ).is_file()
                else {}
            ),
        )
    inputs = GROUPS.load_inputs("my-task", spec, problems_dir=tmp_path)
    assert inputs["group_results"] == {"tl_cpp_brute_draft.cpp": {"1": "OK", "2": "TL"}}
    assert list(inputs["references"]) == ["ma_cpp_sum_draft.cpp"]
