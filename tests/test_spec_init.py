from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from orchestrator import cli
from orchestrator.spec import (
    SPEC_FILENAME,
    SPEC_TEMPLATE,
    ProblemSpec,
    SpecInitError,
    SpecValidationError,
    create_spec_draft,
    load_problem_spec,
    load_spec,
)

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _model_keys(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def _nested_model(model: type[BaseModel], field_name: str) -> type[BaseModel] | None:
    """Модель секции/элемента списка для поля, если оно составное."""
    annotation = model.model_fields[field_name].annotation
    candidates = [annotation, *getattr(annotation, "__args__", ())]
    for candidate in list(candidates):
        candidates.extend(getattr(candidate, "__args__", ()))
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _assert_keys_match(data: dict, model: type[BaseModel], path: str) -> None:
    assert set(data) == _model_keys(model), f"template keys at '{path}' differ from {model.__name__}"
    for key, value in data.items():
        nested = _nested_model(model, key)
        if nested is None:
            continue
        if isinstance(value, dict):
            _assert_keys_match(value, nested, f"{path}.{key}")
        elif isinstance(value, list):
            for item in value:
                _assert_keys_match(item, nested, f"{path}.{key}[]")


def test_template_lists_every_spec_field():
    """Шаблон черновика перечисляет все поля спека — правка формата
    (`ProblemSpec`) без правки шаблона ловится здесь."""
    data = yaml.safe_load((TEMPLATES_DIR / SPEC_TEMPLATE).read_text(encoding="utf-8"))
    _assert_keys_match(data, ProblemSpec, "<root>")


def test_create_spec_draft_fills_problem_id_and_keeps_comments(tmp_path):
    spec_path = create_spec_draft("my-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)

    assert spec_path == tmp_path / "my-task" / SPEC_FILENAME
    text = spec_path.read_text(encoding="utf-8")
    assert yaml.safe_load(text)["problem_id"] == "my-task"
    assert "docs/SPEC_FORMAT.md" in text


def test_draft_fails_validation_on_empty_required_fields(tmp_path):
    """Черновик — не готовый спек: пустые обязательные поля не проходят
    валидацию, а не молча уходят в шаги."""
    spec_path = create_spec_draft("my-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)

    with pytest.raises(SpecValidationError) as exc_info:
        load_spec(spec_path, expected_id="my-task")
    errors = "\n".join(exc_info.value.errors)
    assert "title.ru" in errors
    assert "statement_draft.indexing" in errors


def test_draft_is_valid_once_required_fields_are_filled(tmp_path):
    spec_path = create_spec_draft("my-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    data["title"]["ru"] = "Задача"
    statement = data["statement_draft"]
    statement.update(
        legend_sketch="legend", formal_input_sketch="n", formal_output_sketch="answer",
        indexing="1-indexed",
    )
    statement["sample_examples"][0]["input"] = "1\n"
    data["constraints"].update(intended_complexity="O(N)", complexity_reasoning="one pass")
    data["constraints"]["variables"][0].update(name="N", min=1, description="size")
    data["generation"]["script_style"] = "flat"
    del data["solutions"]
    data["checker"]["standard"] = "ncmp"
    spec_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    assert load_spec(spec_path, expected_id="my-task").problem_id == "my-task"


def test_create_spec_draft_keeps_existing_dir_and_never_overwrites_spec(tmp_path):
    problem_dir = tmp_path / "my-task"
    problem_dir.mkdir()
    (problem_dir / "validator.cpp").write_text("// pulled", encoding="utf-8")

    create_spec_draft("my-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)
    (problem_dir / SPEC_FILENAME).write_text("edited", encoding="utf-8")

    with pytest.raises(SpecInitError, match="already exists"):
        create_spec_draft("my-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)
    assert (problem_dir / SPEC_FILENAME).read_text(encoding="utf-8") == "edited"
    assert (problem_dir / "validator.cpp").read_text(encoding="utf-8") == "// pulled"


def test_create_spec_draft_rejects_bad_problem_id(tmp_path):
    with pytest.raises(SpecInitError, match="must be ASCII"):
        create_spec_draft("1-task", problems_dir=tmp_path, templates_dir=TEMPLATES_DIR)
    assert not (tmp_path / "1-task").exists()


def test_nested_draft_takes_problem_id_from_leaf_in_problems(tmp_path):
    problems = tmp_path / "problems"
    spec_path = create_spec_draft(
        "contest/2026/my-task", problems_dir=problems, templates_dir=TEMPLATES_DIR
    )

    assert spec_path == problems / "contest" / "2026" / "my-task" / SPEC_FILENAME
    assert yaml.safe_load(spec_path.read_text(encoding="utf-8"))["problem_id"] == "my-task"


def test_nested_draft_joins_path_into_problem_id_in_examples(tmp_path):
    examples = tmp_path / "examples"
    spec_path = create_spec_draft("array/sums", problems_dir=examples, templates_dir=TEMPLATES_DIR)

    assert yaml.safe_load(spec_path.read_text(encoding="utf-8"))["problem_id"] == "array--sums"


@pytest.mark.parametrize(
    ("root", "problem_path", "problem_id", "wrong_id"),
    [
        ("problems", "contest/2026/my-task", "my-task", "contest--2026--my-task"),
        ("examples", "array/sums", "array--sums", "sums"),
    ],
)
def test_load_problem_spec_checks_problem_id_derived_from_path(
    tmp_path, root, problem_path, problem_id, wrong_id
):
    problems_dir = tmp_path / root
    spec_path = problems_dir / problem_path / SPEC_FILENAME
    spec_path.parent.mkdir(parents=True)
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")

    spec_path.write_text(content.replace("problem_id: valid-spec", f"problem_id: {problem_id}"))
    assert load_problem_spec(problem_path, problems_dir=problems_dir).problem_id == problem_id

    spec_path.write_text(content.replace("problem_id: valid-spec", f"problem_id: {wrong_id}"))
    with pytest.raises(SpecValidationError, match="does not match"):
        load_problem_spec(problem_path, problems_dir=problems_dir)


def test_create_spec_draft_rejects_duplicate_id_and_nesting(tmp_path):
    problems = tmp_path / "problems"
    create_spec_draft("contest/2025/my-task", problems_dir=problems, templates_dir=TEMPLATES_DIR)

    def init(problem_path: str) -> None:
        create_spec_draft(problem_path, problems_dir=problems, templates_dir=TEMPLATES_DIR)

    with pytest.raises(SpecInitError, match="already used by problems/contest/2025/my-task"):
        init("contest/2026/my-task")
    with pytest.raises(SpecInitError, match="inside another problem"):
        init("contest/2025/my-task/inner")
    with pytest.raises(SpecInitError, match="already contains problem"):
        init("contest")
    assert not (problems / "contest" / "2026").exists()


def test_cli_init_creates_draft_and_fails_on_second_run(tmp_path, capsys):
    argv = [
        "--base-dir",
        str(tmp_path),
        "--templates-dir",
        str(TEMPLATES_DIR),
        "problems/contest/my-task",
        "init",
    ]

    assert cli.main(argv) == 0
    assert (tmp_path / "problems" / "contest" / "my-task" / SPEC_FILENAME).exists()
    assert "created" in capsys.readouterr().out

    assert cli.main(argv) == 1
    assert "already exists" in capsys.readouterr().err
