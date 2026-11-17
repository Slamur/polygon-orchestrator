import re
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.model_router import ModelResponse
from orchestrator.spec import TestGroupHint as GroupHint
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.constraints_pick import (
    check_test_groups_artifact,
    compute_input_hash,
    extra_context_documents,
    run_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def test_extra_context_documents_is_none(spec):
    assert extra_context_documents(spec) is None


def test_confirmed_writes_constraints_yaml(spec, tmp_path):
    response = ModelResponse(
        status="confirmed", artifacts={"constraints.yaml": "n_max: 100000"}, notes=[]
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    written_path = tmp_path / "valid-spec" / "authoring" / "constraints.yaml"
    assert written_path.read_text(encoding="utf-8") == "n_max: 100000"

    user_prompt = mock_call.call_args.args[2]
    assert "O((N + K) log N)" in user_prompt


@pytest.mark.slow
def test_confirmed_writes_validator_cpp_and_compiles(spec, tmp_path):
    valid_validator = (REPO_ROOT / "examples/array/prefix-sums/validator.cpp").read_text(
        encoding="utf-8"
    )
    response = ModelResponse(
        status="confirmed",
        artifacts={"constraints.yaml": "n_max: 100000", "validator.cpp": valid_validator},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    validator_path = tmp_path / "valid-spec" / "validator.cpp"
    assert validator_path.read_text(encoding="utf-8") == valid_validator
    assert result.compile_results["validator.cpp"].success is True
    if result.compile_results["validator.cpp"].binary_path:
        result.compile_results["validator.cpp"].binary_path.unlink(missing_ok=True)


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[
            {
                "field": "constraints.intended_complexity",
                "kind": "uncertain",
                "explanation": "empty",
            }
        ],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "valid-spec",
                spec,
                None,
                prompts_dir=PROMPTS_DIR,
                problems_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()
    assert not (tmp_path / "valid-spec" / "validator.cpp").exists()


def test_user_prompt_includes_test_groups_needed_false_by_default(spec, tmp_path):
    response = ModelResponse(
        status="confirmed", artifacts={"constraints.yaml": "n_max: 100000"}, notes=[]
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "test_groups_needed: False" in user_prompt


def test_test_groups_needed_false_no_hint_yields_no_test_groups_in_artifact(spec, tmp_path):
    spec_no_groups = spec.model_copy(
        update={
            "constraints": spec.constraints.model_copy(
                update={"test_groups_hint": None, "test_groups_needed": False}
            )
        }
    )
    response = ModelResponse(
        status="confirmed", artifacts={"constraints.yaml": "n_max: 100000\n"}, notes=[]
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec_no_groups,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    user_prompt = mock_call.call_args.args[2]
    assert "test_groups_needed: False" in user_prompt
    written = (tmp_path / "valid-spec" / "authoring" / "constraints.yaml").read_text(
        encoding="utf-8"
    )
    assert "test_groups" not in written


def test_test_groups_needed_true_no_hint_proposes_groups(spec, tmp_path):
    spec_needed = spec.model_copy(
        update={
            "constraints": spec.constraints.model_copy(
                update={"test_groups_hint": None, "test_groups_needed": True}
            )
        }
    )
    response = ModelResponse(
        status="proposed",
        artifacts={"constraints.yaml": ('test_groups:  # PROPOSED, REVIEW ME\n  - name: "1"\n')},
        notes=[
            {
                "field": "constraints.test_groups",
                "kind": "proposed",
                "explanation": "no hint given, a reasonable small/edge/max set is proposed",
            }
        ],
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec_needed,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "proposed"
    user_prompt = mock_call.call_args.args[2]
    assert "test_groups_needed: True" in user_prompt
    written = (tmp_path / "valid-spec" / "authoring" / "constraints.yaml").read_text(
        encoding="utf-8"
    )
    assert "test_groups" in written


def test_test_groups_hint_wins_over_needed_false(spec, tmp_path):
    # The `spec` fixture already sets test_groups_hint; test_groups_needed is False by default —
    # the hint must form groups regardless of the flag.
    assert spec.constraints.test_groups_needed is False
    assert spec.constraints.test_groups_hint

    response = ModelResponse(
        status="confirmed",
        artifacts={"constraints.yaml": "test_groups:\n  - name: small\n"},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "test_groups_needed: False" in user_prompt
    assert "name=1," in user_prompt


def test_renders_without_crashing_when_optional_lists_are_null(spec, tmp_path):
    spec_bare = spec.model_copy(
        update={
            "constraints": spec.constraints.model_copy(
                update={
                    "special_guarantees": None,
                    "test_groups_hint": None,
                    "uncertain_points": None,
                }
            )
        }
    )
    response = ModelResponse(status="confirmed", artifacts={"constraints.yaml": "ok"}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_bare,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"


def test_user_prompt_includes_input_format_but_not_legend(spec, tmp_path):
    response = ModelResponse(status="confirmed", artifacts={}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    statement = spec.statement_draft
    assert statement.formal_input_sketch.strip() in user_prompt
    assert f"Indexing: {statement.indexing}" in user_prompt
    for ex in statement.sample_examples:
        assert ex.input.strip() in user_prompt
    # deriving the asymptotics from the legend is forbidden — the step doesn't get the legend
    assert statement.legend_sketch.strip() not in user_prompt


@pytest.mark.parametrize(
    "field, value, changes",
    [
        ("formal_input_sketch", "One line: N.", True),
        ("indexing", "0-indexed", True),
        ("legend_sketch", "Another legend.", False),
        ("formal_output_sketch", "Print YES or NO.", False),
    ],
)
def test_compute_input_hash_depends_only_on_input_part_of_statement(spec, field, value, changes):
    h1 = compute_input_hash("valid-spec", spec)

    spec_changed = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={field: value})}
    )
    h2 = compute_input_hash("valid-spec", spec_changed)

    assert (h1 != h2) == changes


def test_problem_id_in_constraints_yaml_comes_from_spec(spec, tmp_path):
    # The problem directory is nested in a group: the path `contest/valid-spec` isn't the problem_id.
    response = ModelResponse(
        status="confirmed",
        artifacts={
            "constraints.yaml": "problem_id: contest/valid-spec   # from the response\nn_max: 100000\n"
        },
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "contest/valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    user_prompt = mock_call.call_args.args[2]
    assert "problem_id: valid-spec)" in user_prompt
    assert "contest/" not in user_prompt
    written = (tmp_path / "contest" / "valid-spec" / "authoring" / "constraints.yaml").read_text(
        encoding="utf-8"
    )
    assert written == "problem_id: valid-spec           # from the response\nn_max: 100000\n"
    assert [note["field"] for note in result.notes] == ["problem_id"]


def test_correct_problem_id_in_constraints_yaml_is_left_as_is(spec, tmp_path):
    text = "problem_id: valid-spec\nn_max: 100000\n"
    response = ModelResponse(status="confirmed", artifacts={"constraints.yaml": text}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert (tmp_path / "valid-spec" / "authoring" / "constraints.yaml").read_text(
        encoding="utf-8"
    ) == text
    assert result.notes == []


def _groups_yaml(groups: str, validator_groups: str = "") -> str:
    text = "problem_id: valid-spec\ntest_groups:\n" + groups
    if validator_groups:
        text += "validator:\n  constants: []\n  groups:\n" + validator_groups
    return text


_TWO_GROUPS = (
    '  - name: "1"\n    points: 40\n    dependencies: []\n'
    '  - name: "2"\n    points: 60\n    dependencies: ["1"]\n'
)


def test_test_groups_check_accepts_valid_groups(spec):
    check_test_groups_artifact(_groups_yaml(_TWO_GROUPS, '    - name: "1"\n'), spec)
    check_test_groups_artifact("problem_id: valid-spec\n", spec)


@pytest.mark.parametrize(
    "text, error",
    [
        (_groups_yaml('  - name: "a"\n'), "must be a positive number"),
        (_groups_yaml('  - name: "1"\n  - name: "1"\n'), "group names repeat"),
        (
            _groups_yaml('  - name: "1"\n    dependencies: ["2"]\n  - name: "2"\n'),
            "group 1 depends on group 2, which isn't an earlier group",
        ),
        (
            _groups_yaml('  - name: "1"\n    points: 40\n  - name: "2"\n'),
            "groups 2 have no points",
        ),
        (
            _groups_yaml('  - name: "1"\n    points: 40\n  - name: "2"\n    points: 50\n'),
            "sum to 90, not 100",
        ),
        (
            _groups_yaml(_TWO_GROUPS, '    - name: "3"\n'),
            "validator.groups has '3', which isn't one of test_groups",
        ),
        (
            _groups_yaml(_TWO_GROUPS, '    - name: "1"\n    - name: "1"\n'),
            "validator.groups has group 1 twice",
        ),
        (
            'validator:\n  groups:\n    - name: "1"\n',
            "validator.groups is set, but there are no test_groups",
        ),
    ],
)
def test_test_groups_check_rejects_invalid_groups(spec, text, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        check_test_groups_artifact(text, spec)


def test_test_groups_check_accepts_the_authors_total_of_points(spec):
    hint = [
        GroupHint(name="1", points=30),
        GroupHint(name="2", points=60, dependencies=["1"]),
    ]
    author = spec.model_copy(
        update={"constraints": spec.constraints.model_copy(update={"test_groups_hint": hint})}
    )
    text = _groups_yaml(
        '  - name: "1"\n    points: 30\n  - name: "2"\n    points: 60\n    dependencies: ["1"]\n'
    )

    check_test_groups_artifact(text, author)
    with pytest.raises(ValueError, match="sum to 90"):
        check_test_groups_artifact(text, spec)


def test_constraints_with_invalid_test_groups_are_not_written(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"constraints.yaml": _groups_yaml('  - name: "1"\n    points: 50\n')},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            None,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert not (tmp_path / "valid-spec" / "authoring" / "constraints.yaml").exists()
    assert "sum to 50, not 100" in result.not_applied_reason
    assert (tmp_path / "valid-spec" / ".cache" / "constraints_pick.response.json").is_file()
