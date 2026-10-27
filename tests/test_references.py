from pathlib import Path

import pytest

from orchestrator.references import SpecReferenceError, reference_documents
from orchestrator.spec import SpecValidationError, load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    generation = spec.generation.model_copy(update={"references": None})
    return spec.model_copy(update={"generation": generation})


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _reference_dir(tmp_path: Path, name: str = "example-problem") -> Path:
    """Каталог в раскладке problems/<problem_id>/ со своим spec.yaml."""
    directory = tmp_path / name
    _write(directory / "authoring" / "spec.yaml")
    _write(directory / "authoring" / "constraints.yaml")
    _write(directory / "validator.cpp")
    _write(directory / "generators" / "gen_b.cpp")
    _write(directory / "generators" / "gen_a.cpp")
    _write(directory / "test_script")
    return directory


def _with_common(spec, refs):
    return spec.model_copy(update={"references": [str(ref) for ref in refs]})


def _with_section(spec, section, refs):
    updated = getattr(spec, section).model_copy(update={"references": [str(ref) for ref in refs]})
    return spec.model_copy(update={section: updated})


def test_no_references_gives_none(spec):
    assert reference_documents("constraints_pick", spec) is None


def test_directory_gives_only_files_of_the_step(spec, tmp_path):
    directory = _reference_dir(tmp_path)

    documents = reference_documents("generators_and_script", _with_common(spec, [directory]))

    assert documents == [
        str(directory / "authoring" / "spec.yaml"),
        str(directory / "authoring" / "constraints.yaml"),
        str(directory / "generators" / "gen_a.cpp"),
        str(directory / "generators" / "gen_b.cpp"),
        str(directory / "test_script"),
    ]
    assert reference_documents("constraints_pick", _with_common(spec, [directory])) == [
        str(directory / "authoring" / "spec.yaml"),
        str(directory / "authoring" / "constraints.yaml"),
        str(directory / "validator.cpp"),
    ]


def test_directory_in_layout_before_authoring_still_gives_spec_and_constraints(spec, tmp_path):
    # Задача-референс, ещё не перенесённая в authoring/ (её не трогала ни
    # одна команда после обновления).
    directory = tmp_path / "old-problem"
    _write(directory / "spec.yaml")
    _write(directory / "constraints.yaml")
    _write(directory / "validator.cpp")

    documents = reference_documents("constraints_pick", _with_common(spec, [directory]))

    assert documents == [
        str(directory / "spec.yaml"),
        str(directory / "constraints.yaml"),
        str(directory / "validator.cpp"),
    ]


def test_files_marked_removed_are_not_references(spec, tmp_path):
    directory = _reference_dir(tmp_path)
    _write(directory / "solutions" / "ok_cpp_main_draft.cpp")
    _write(directory / "solutions" / "wa_cpp_old_draft.cpp.removed")
    _write(directory / "generators" / "gen_old.cpp.removed")

    common = _with_common(spec, [directory])

    assert str(directory / "generators" / "gen_old.cpp.removed") not in reference_documents(
        "generators_and_script", common
    )
    assert reference_documents("solutions_draft", common) == [
        str(directory / "authoring" / "spec.yaml"),
        str(directory / "solutions" / "ok_cpp_main_draft.cpp"),
    ]


def test_common_directory_without_files_for_the_step_is_skipped(spec, tmp_path):
    # Нет checker.cpp — шагу checker_draft этот каталог ничего не даёт, и
    # spec.yaml без результата тоже не прикладывается.
    directory = _reference_dir(tmp_path)

    assert reference_documents("checker_draft", _with_common(spec, [directory])) is None


def test_directory_without_spec_is_still_a_reference(spec, tmp_path):
    directory = _reference_dir(tmp_path)
    (directory / "authoring" / "spec.yaml").unlink()

    documents = reference_documents("checker_draft", _with_section(spec, "checker", [directory / "validator.cpp"]))

    assert documents == [str(directory / "validator.cpp")]
    assert reference_documents("constraints_pick", _with_common(spec, [directory])) == [
        str(directory / "authoring" / "constraints.yaml"),
        str(directory / "validator.cpp"),
    ]


def test_common_references_come_before_section_references(spec, tmp_path):
    common = _reference_dir(tmp_path, "common-ref")
    section_file = _write(tmp_path / "my_examples" / "gen_tree.cpp")
    spec = _with_section(_with_common(spec, [common]), "generation", [section_file])

    documents = reference_documents("generators_and_script", spec)

    assert documents[0] == str(common / "authoring" / "spec.yaml")
    assert documents[-1] == str(section_file)


def test_common_file_reference_goes_to_every_step(spec, tmp_path):
    file_ref = _write(tmp_path / "notes.md")
    spec = _with_common(spec, [file_ref])

    for step_name in ["statement_draft", "constraints_pick", "checker_draft"]:
        assert reference_documents(step_name, spec) == [str(file_ref)]


def test_missing_path_raises(spec, tmp_path):
    with pytest.raises(SpecReferenceError, match="references: '.*no-such-dir' not found"):
        reference_documents("constraints_pick", _with_common(spec, [tmp_path / "no-such-dir"]))
    with pytest.raises(SpecReferenceError, match="constraints.references"):
        reference_documents("constraints_pick", _with_section(spec, "constraints", [tmp_path / "x.cpp"]))


def test_section_directory_without_files_for_the_step_raises(spec, tmp_path):
    directory = _reference_dir(tmp_path)

    with pytest.raises(SpecReferenceError, match="checker.references: directory .* no files for step 'checker_draft'"):
        reference_documents("checker_draft", _with_section(spec, "checker", [directory]))


def test_common_directory_without_files_for_any_step_raises(spec, tmp_path):
    directory = tmp_path / "unrelated"
    _write(directory / "README.md")

    with pytest.raises(SpecReferenceError, match="none of the files any step takes"):
        reference_documents("constraints_pick", _with_common(spec, [directory]))


def test_keep_filters_directory_files_only(spec, tmp_path):
    directory = _reference_dir(tmp_path)
    file_ref = _write(tmp_path / "gen_extra.cpp")
    spec = _with_section(_with_common(spec, [directory]), "generation", [file_ref])

    documents = reference_documents(
        "generators_and_script", spec, keep=lambda rel_path: not rel_path.startswith("generators/")
    )

    assert documents == [
        str(directory / "authoring" / "spec.yaml"),
        str(directory / "authoring" / "constraints.yaml"),
        str(directory / "test_script"),
        str(file_ref),
    ]


def test_spec_rejects_old_base_template_refs_with_hint(tmp_path):
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    path = tmp_path / "valid-spec.yaml"
    path.write_text(text.replace("  references:\n", "  base_template_refs:\n", 1), encoding="utf-8")

    with pytest.raises(SpecValidationError, match="renamed to generation.references"):
        load_spec(path)
