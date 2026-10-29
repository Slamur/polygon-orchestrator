from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from orchestrator.cache import compute_input_values
from orchestrator.model_router import ModelResponse
from orchestrator.spec import load_spec
from orchestrator.steps.generators_draft import (
    compute_input_hash,
    generator_params,
    hash_inputs,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PLAN_YAML = (FIXTURES_DIR / "tests-plan.yaml").read_text(encoding="utf-8")
VALID_GENERATOR = (FIXTURES_DIR / "cpp" / "valid_generator.cpp").read_text(encoding="utf-8")


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _upstream(plan_yaml=PLAN_YAML):
    return {"constraints.yaml": "n_max: 100000\n", "tests_plan.yaml": plan_yaml}


def _plan_with(**generator_changes) -> str:
    plan = yaml.safe_load(PLAN_YAML)
    plan["generators"][0].update(generator_changes)
    return yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)


def _run(spec, tmp_path, responses, plan_yaml=PLAN_YAML):
    with patch("orchestrator.model_router.call_model", side_effect=responses) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            _upstream(plan_yaml),
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    for compiled in result.compile_results.values():
        if compiled.binary_path:
            compiled.binary_path.unlink(missing_ok=True)
    return result, mock_call


def test_generator_params_reads_opt_names():
    source = 'int n = opt<int>("n");\nll k = opt<ll>( "maxK" );\nbool d = opt<bool>("n");'
    assert generator_params(source) == ["n", "maxK"]


def test_missing_plan_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="tests_plan.yaml"):
        run_step("valid-spec", spec, {"constraints.yaml": "n: 1"}, problems_dir=tmp_path)
    with pytest.raises(ValueError, match="fix authoring/tests_plan.yaml"):
        run_step("valid-spec", spec, _upstream("generators: []"), problems_dir=tmp_path)


def test_writes_generators_of_the_plan(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={"generators/gen_rand.cpp": VALID_GENERATOR})

    result, mock_call = _run(spec, tmp_path, [response])

    generators_dir = tmp_path / "valid-spec" / "generators"
    assert (generators_dir / "gen_rand.cpp").read_text(encoding="utf-8") == VALID_GENERATOR
    assert not (generators_dir / "generators").exists()
    assert result.notes == []
    user_prompt = mock_call.call_args.args[2]
    assert "### gen_rand.cpp" in user_prompt
    assert "Случайный массив длины n." in user_prompt
    # Тесты плана генераторам не нужны.
    assert "Случайные максимальные тесты" not in user_prompt


def test_missing_generator_is_asked_for_in_a_separate_call(spec, tmp_path):
    plan = yaml.safe_load(PLAN_YAML)
    plan["generators"].append({"name": "upd_shuffle", "status": "confirmed", "params": []})
    plan["tests"][0]["commands"].append("gen_rand -n=maxN | upd_shuffle")
    first = ModelResponse(status="confirmed", artifacts={"gen_rand.cpp": VALID_GENERATOR})
    retry = ModelResponse(status="confirmed", artifacts={"upd_shuffle.cpp": "int main() {}\n"})

    result, mock_call = _run(spec, tmp_path, [first, retry], plan_yaml=yaml.safe_dump(plan))

    assert mock_call.call_count == 2
    assert "upd_shuffle.cpp" in mock_call.call_args.args[2]
    assert (tmp_path / "valid-spec" / "generators" / "upd_shuffle.cpp").exists()
    assert "retried" in [note["kind"] for note in result.notes]


def test_notes_generators_that_differ_from_the_plan(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"gen_rand.cpp": VALID_GENERATOR.replace('opt<int>("n")', 'opt<int>("count")')},
    )
    (tmp_path / "valid-spec" / "generators").mkdir(parents=True)
    (tmp_path / "valid-spec" / "generators" / "gen_old.cpp").write_text("x", encoding="utf-8")

    result, _ = _run(spec, tmp_path, [response])

    mismatches = {n["field"]: n["explanation"] for n in result.notes if n["kind"] == "plan_mismatch"}
    assert set(mismatches) == {"generators/gen_old.cpp", "generators/gen_rand.cpp"}
    assert "not in tests_plan.yaml" in mismatches["generators/gen_old.cpp"]
    assert "['count']" in mismatches["generators/gen_rand.cpp"]


def test_input_parts_are_per_generator_and_ignore_tests_of_the_plan(spec):
    plan = yaml.safe_load(PLAN_YAML)
    plan["tests"][0]["description"] = "другое описание"
    other_tests = yaml.safe_dump(plan, allow_unicode=True, sort_keys=False)
    other_algorithm = _plan_with(algorithm="Другой алгоритм.\n")

    base = compute_input_hash("valid-spec", spec, _upstream())
    assert compute_input_hash("valid-spec", spec, _upstream(other_tests)) == base
    assert compute_input_hash("valid-spec", spec, _upstream(other_algorithm)) != base
    values = compute_input_values(*hash_inputs("valid-spec", spec, _upstream()))
    assert "upstream: tests_plan.yaml: gen_rand" in values
