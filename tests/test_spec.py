from pathlib import Path

import pytest

from orchestrator.spec import ProblemSpec, SpecValidationError, load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_load_valid_spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")

    assert isinstance(spec, ProblemSpec)
    assert spec.problem_id == "valid-spec"
    assert spec.title.ru == "Кратчайшие пути"
    assert spec.constraints.intended_complexity == "O((N + K) log N)"
    assert spec.constraints.variables[0].name == "N"
    assert spec.generation.script_style == "groups"
    assert spec.solutions is not None
    assert spec.solutions.wanted_verdicts == ["ok", "wa", "tl"]
    assert spec.checker.custom_needed is False
    assert spec.meta.status == "draft"


def test_missing_intended_complexity_raises_with_clear_message():
    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(FIXTURES_DIR / "missing-intended-complexity.yaml")

    error = exc_info.value
    joined = "\n".join(error.errors)
    assert "constraints.intended_complexity" in joined
    assert "constraints_pick" in joined


def test_missing_statement_draft_required_fields_raises():
    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(FIXTURES_DIR / "missing-statement-fields.yaml")

    error = exc_info.value
    joined = "\n".join(error.errors)
    assert "statement_draft.formal_input_sketch" in joined
    assert "statement_draft.formal_output_sketch" in joined
    assert "statement_draft.sample_examples" in joined


def test_missing_file_raises_spec_validation_error(tmp_path):
    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(tmp_path / "does-not-exist.yaml")

    assert "not found" in str(exc_info.value)


def test_problem_id_mismatch_with_filename_raises(tmp_path):
    bad_path = tmp_path / "other-name.yaml"
    bad_path.write_text(
        (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(bad_path)

    assert "does not match the file name" in str(exc_info.value)


def test_specific_test_ideas_loaded():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")

    assert spec.generation.specific_test_ideas == [
        "N = 1 (минимальный случай)",
        "N = N_max, значения максимальные",
    ]


def test_specific_test_ideas_absent_defaults_to_none(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace(
        '  specific_test_ideas:\n'
        '    - "N = 1 (минимальный случай)"\n'
        '    - "N = N_max, значения максимальные"\n',
        "",
    )
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")

    spec = load_spec(path)

    assert spec.generation.specific_test_ideas is None


def test_preserve_legend_verbatim_absent_defaults_to_false():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")

    assert spec.statement_draft.preserve_legend_verbatim is False


def test_preserve_legend_verbatim_true_is_loaded(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace(
        '  indexing: "1-indexed"\n',
        '  indexing: "1-indexed"\n  preserve_legend_verbatim: true\n',
    )
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")

    spec = load_spec(path)

    assert spec.statement_draft.preserve_legend_verbatim is True


def _write_with_indexing(tmp_path, value: str) -> Path:
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace('  indexing: "1-indexed"\n', f'  indexing: "{value}"\n')
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_indexing_irrelevant_is_accepted(tmp_path):
    spec = load_spec(_write_with_indexing(tmp_path, "irrelevant"))

    assert spec.statement_draft.indexing == "irrelevant"


def test_indexing_unknown_value_is_rejected(tmp_path):
    with pytest.raises(SpecValidationError, match="statement_draft.indexing"):
        load_spec(_write_with_indexing(tmp_path, "не имеет значения"))


def test_test_groups_needed_absent_defaults_to_false():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")

    assert spec.constraints.test_groups_needed is False


def test_test_groups_needed_true_is_loaded(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace(
        '  intended_complexity: "O((N + K) log N)"\n',
        '  intended_complexity: "O((N + K) log N)"\n  test_groups_needed: true\n',
    )
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")

    spec = load_spec(path)

    assert spec.constraints.test_groups_needed is True


def test_custom_checker_without_notes_raises(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace(
        'checker:\n  standard: "ncmp"\n  custom_needed: false\n  custom_comparison_notes: null',
        'checker:\n  standard: null\n  custom_needed: true\n  custom_comparison_notes: null',
    )
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(path)

    assert "custom_comparison_notes" in str(exc_info.value)


def test_generation_input_shape_is_forbidden(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    text = text.replace(
        "generation:\n",
        "generation:\n  input_shape: |\n    Первая строка: N K.\n",
    )
    assert "input_shape" in text
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(path)

    joined = "\n".join(exc_info.value.errors)
    assert "generation.input_shape" in joined


def test_spec_without_generation_input_shape_loads():
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    assert "input_shape" not in text

    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")

    assert not hasattr(spec.generation, "input_shape")


def _spec_without_first_sample_output(tmp_path, replacement: str) -> Path:
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    original = "      output: |\n        8\n"
    assert original in text
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text.replace(original, replacement), encoding="utf-8")
    return path


@pytest.mark.parametrize("replacement", ["", "      output: |\n"], ids=["key-absent", "empty-block"])
def test_sample_example_output_is_optional(tmp_path, replacement):
    spec = load_spec(_spec_without_first_sample_output(tmp_path, replacement))

    example = spec.statement_draft.sample_examples[0]
    assert not example.output
    assert example.input.strip() == "3 2\n1 2 5\n2 3 3\n1 3".strip()


def test_sample_example_input_still_required(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    original = "    - input: |\n        3 2\n        1 2 5\n        2 3 3\n        1 3\n"
    assert original in text
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text.replace(original, "    - input: \"\"\n"), encoding="utf-8")

    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(path)

    assert "sample_examples[].input" in "\n".join(exc_info.value.errors)
