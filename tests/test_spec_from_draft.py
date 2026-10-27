"""`init --ref` / `init --draft`: спек по задаче-основе и по черновику автора.
Модель подменена (`model_router.call_model`) — реальных вызовов нет."""

from pathlib import Path

import pytest
import yaml

from orchestrator import cli, model_router
from orchestrator.drafts import DraftError, draft_names, find_draft
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import AUTHORING_DIRNAME
from orchestrator.spec import (
    SPEC_FILENAME,
    SpecInitError,
    add_reference,
    create_spec_draft,
    prepare_spec_init,
    set_problem_id,
)
from orchestrator.spec_from_draft import (
    SPEC_FORMAT_DOC,
    context_documents,
    generate_spec_from_draft,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

DRAFT_TEXT = "Синтетический черновик: дан массив, найти сумму.\n"


@pytest.fixture
def base_dir(tmp_path):
    """Каталог с `problems/`, `examples/` (одна учебная задача со спеком) и
    `drafts/`."""
    example = tmp_path / "examples" / "array" / "sums"
    example.mkdir(parents=True)
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    (example / AUTHORING_DIRNAME).mkdir()
    (example / AUTHORING_DIRNAME / SPEC_FILENAME).write_text(
        content.replace("problem_id: valid-spec", "problem_id: array--sums"), encoding="utf-8"
    )
    (tmp_path / "problems").mkdir()
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "my-idea.md").write_text(DRAFT_TEXT, encoding="utf-8")
    return tmp_path


class _FakeModel:
    def __init__(self, response: ModelResponse):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, step, system_prompt, user_prompt, context_documents=None):
        self.calls.append(
            dict(step=step, system=system_prompt, user=user_prompt, documents=context_documents)
        )
        return self.response


@pytest.fixture
def fake_model(monkeypatch):
    def install(response: ModelResponse) -> _FakeModel:
        fake = _FakeModel(response)
        monkeypatch.setattr(model_router, "call_model", fake)
        return fake

    return install


def _valid_spec_text(problem_id: str = "whatever") -> str:
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    return content.replace("problem_id: valid-spec", f"problem_id: {problem_id}")


# --- drafts/ ---------------------------------------------------------------


def test_find_draft_by_name_with_or_without_extension(base_dir):
    drafts = base_dir / "drafts"
    (drafts / ".gitkeep").write_text("", encoding="utf-8")

    assert draft_names(drafts) == ["my-idea.md"]
    assert find_draft(drafts, "my-idea.md") == drafts / "my-idea.md"
    assert find_draft(drafts, "my-idea") == drafts / "my-idea.md"


def test_find_draft_rejects_ambiguous_missing_and_empty(base_dir):
    drafts = base_dir / "drafts"
    (drafts / "my-idea.txt").write_text("other", encoding="utf-8")
    (drafts / "blank.txt").write_text("  \n", encoding="utf-8")

    with pytest.raises(DraftError, match="ambiguous"):
        find_draft(drafts, "my-idea")
    assert find_draft(drafts, "my-idea.txt") == drafts / "my-idea.txt"
    with pytest.raises(DraftError, match="not found"):
        find_draft(drafts, "nope")
    with pytest.raises(DraftError, match="empty"):
        find_draft(drafts, "blank")


# --- правка текста спека ----------------------------------------------------


def test_set_problem_id_keeps_comment_column():
    text = 'problem_id: array--sums    # comment\ntitle: {ru: "x"}\n'

    result = set_problem_id(text, "my-task")

    assert yaml.safe_load(result)["problem_id"] == "my-task"
    assert result.index("#") == text.index("#")
    assert set_problem_id('problem_id: ""\n', "my-task") == "problem_id: my-task\n"
    with pytest.raises(SpecInitError):
        set_problem_id("title: x\n", "my-task")


@pytest.mark.parametrize(
    ("text", "expected_line"),
    [
        ("problem_id: p\nreferences: []  # hint\n", 'references: ["r"]  # hint'),
        ("problem_id: p\nreferences: null\n", 'references: ["r"]'),
        ('problem_id: p\nreferences: ["a"]\n', 'references: ["a", "r"]'),
    ],
)
def test_add_reference_to_flow_list(text, expected_line):
    result = add_reference(text, "r")

    assert expected_line in result.splitlines()


def test_add_reference_to_block_list_and_missing_field():
    block = 'problem_id: p\nreferences:\n    - "a"\n# section\nstatement_draft:\n  x: 1\n'
    assert add_reference(block, "r") == (
        'problem_id: p\nreferences:\n    - "a"\n    - "r"\n# section\nstatement_draft:\n  x: 1\n'
    )

    missing = "problem_id: p\n\n# section\nstatement_draft:\n  x: 1\n"
    assert add_reference(missing, "r") == (
        'problem_id: p\nreferences:\n  - "r"\n\n# section\nstatement_draft:\n  x: 1\n'
    )

    already = 'problem_id: p\nreferences: ["r"]\n'
    assert add_reference(already, "r") == already


# --- init --ref без модели ---------------------------------------------------


def test_create_spec_draft_copies_reference_spec(base_dir):
    spec_path = create_spec_draft(
        "my-task",
        problems_dir=base_dir / "problems",
        templates_dir=TEMPLATES_DIR,
        reference="examples/array/sums/",
    )

    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    original = yaml.safe_load(
        (base_dir / "examples" / "array" / "sums" / AUTHORING_DIRNAME / SPEC_FILENAME).read_text(encoding="utf-8")
    )
    assert data["problem_id"] == "my-task"
    assert data["references"] == [*(original.get("references") or []), "examples/array/sums"]
    assert data["statement_draft"] == original["statement_draft"]


def test_prepare_spec_init_rejects_bad_reference(base_dir):
    problems = base_dir / "problems"

    with pytest.raises(SpecInitError, match="--ref"):
        prepare_spec_init("my-task", problems_dir=problems, templates_dir=TEMPLATES_DIR, reference="array/sums")
    with pytest.raises(SpecInitError, match="failed to read the spec of examples/array/none"):
        prepare_spec_init(
            "my-task", problems_dir=problems, templates_dir=TEMPLATES_DIR, reference="examples/array/none"
        )
    assert not (problems / "my-task").exists()


def test_cli_init_ref_without_draft_does_not_call_the_model(base_dir, fake_model, capsys):
    fake = fake_model(ModelResponse(status="confirmed"))

    code = cli.main(
        [
            "--base-dir", str(base_dir), "--templates-dir", str(TEMPLATES_DIR),
            "problems/my-task", "init", "--ref", "examples/array/sums",
        ]
    )

    assert code == 0
    assert fake.calls == []
    assert "as a copy of the spec of examples/array/sums" in capsys.readouterr().out
    assert (base_dir / "problems" / "my-task" / AUTHORING_DIRNAME / SPEC_FILENAME).exists()


# --- init --draft ------------------------------------------------------------


def _init(base_dir, reference=None):
    return prepare_spec_init(
        "my-task", problems_dir=base_dir / "problems", templates_dir=TEMPLATES_DIR, reference=reference
    )


def _generate(base_dir, init):
    return generate_spec_from_draft(
        init,
        base_dir / "drafts" / "my-idea.md",
        prompts_dir=PROMPTS_DIR,
        templates_dir=TEMPLATES_DIR,
        examples_dir=base_dir / "examples",
    )


def test_draft_spec_is_written_with_problem_id_and_reference_set_by_orchestrator(
    base_dir, fake_model
):
    # Модель вернула чужой problem_id и не добавила референс — оркестратор
    # ставит их сам.
    fake = fake_model(
        ModelResponse(
            status="proposed",
            artifacts={SPEC_FILENAME: _valid_spec_text("wrong-id")},
            notes=[{"field": "constraints.intended_complexity", "kind": "proposed", "explanation": "x"}],
        )
    )

    result = _generate(base_dir, _init(base_dir, reference="examples/array/sums"))

    problem_dir = base_dir / "problems" / "my-task"
    assert result.path == problem_dir / AUTHORING_DIRNAME / SPEC_FILENAME
    data = yaml.safe_load(result.path.read_text(encoding="utf-8"))
    assert data["problem_id"] == "my-task"
    assert "examples/array/sums" in data["references"]
    assert result.validation_errors == []
    assert result.draft_copy == problem_dir / AUTHORING_DIRNAME / "draft.md"
    assert result.draft_copy.read_text(encoding="utf-8") == DRAFT_TEXT

    (call,) = fake.calls
    assert call["step"] == "spec_from_draft"
    assert DRAFT_TEXT.strip() in call["user"]
    assert "examples/array/sums" in call["user"]
    assert "problem_id: my-task" in call["user"]
    # Спек задачи-основы — уже в user-промпте, вторым документом не идёт.
    assert set(call["documents"]) == {SPEC_FORMAT_DOC.as_posix(), "spec_template.yaml"}


def test_draft_spec_from_template_attaches_example_specs(base_dir, fake_model):
    fake = fake_model(ModelResponse(status="proposed", artifacts={SPEC_FILENAME: "problem_id: x\n"}))

    result = _generate(base_dir, _init(base_dir))

    (call,) = fake.calls
    assert "examples/array/sums/spec.yaml" in call["documents"]
    assert "шаблон спека" in call["user"]
    # Невалидный спек всё равно записан — это черновик, вызов не пропадает.
    assert result.path.read_text(encoding="utf-8") == "problem_id: my-task\n"
    assert result.validation_errors


def test_draft_without_spec_in_response_writes_nothing(base_dir, fake_model):
    fake_model(
        ModelResponse(
            status="uncertain",
            notes=[{"field": "draft", "kind": "uncertain", "explanation": "not a problem"}],
        )
    )

    result = _generate(base_dir, _init(base_dir))

    assert result.path is None
    assert result.status == "uncertain"
    assert not (base_dir / "problems" / "my-task").exists()


def test_context_documents_include_format_and_template():
    documents = context_documents(templates_dir=TEMPLATES_DIR, examples_dir=REPO_ROOT / "examples")

    assert documents[SPEC_FORMAT_DOC.as_posix()].startswith("# Формат входного спека")
    assert "spec_template.yaml" in documents
    assert "examples/array/prefix-sums/spec.yaml" in documents


def _cli_draft(base_dir, *extra):
    return cli.main(
        [
            "--base-dir", str(base_dir), "--templates-dir", str(TEMPLATES_DIR),
            "--prompts-dir", str(PROMPTS_DIR), "problems/my-task", "init", "--draft", "my-idea",
            *extra,
        ]
    )


def test_cli_init_draft_writes_spec_and_log(base_dir, fake_model, capsys):
    fake_model(ModelResponse(status="proposed", artifacts={SPEC_FILENAME: _valid_spec_text()}))

    assert _cli_draft(base_dir) == 0

    out = capsys.readouterr().out
    problem_dir = base_dir / "problems" / "my-task"
    assert "review the PROPOSED / SUGGESTION marks" in out
    assert (problem_dir / AUTHORING_DIRNAME / "draft.md").exists()
    assert "init --draft my-idea" in (problem_dir / "llm_generation.log").read_text(encoding="utf-8")


def test_cli_init_draft_checks_everything_before_calling_the_model(base_dir, fake_model, capsys):
    fake = fake_model(ModelResponse(status="proposed", artifacts={SPEC_FILENAME: _valid_spec_text()}))
    problem_dir = base_dir / "problems" / "my-task"

    assert _cli_draft(base_dir, "--ref", "examples/array/none") == 1
    assert cli.main(["--base-dir", str(base_dir), "problems/my-task", "init", "--draft", "nope"]) == 1
    assert not problem_dir.exists()

    (problem_dir / AUTHORING_DIRNAME).mkdir(parents=True)
    (problem_dir / AUTHORING_DIRNAME / SPEC_FILENAME).write_text("edited", encoding="utf-8")
    assert _cli_draft(base_dir) == 1
    assert "already exists" in capsys.readouterr().err
    assert (problem_dir / AUTHORING_DIRNAME / SPEC_FILENAME).read_text(encoding="utf-8") == "edited"
    assert fake.calls == []


def test_spec_from_draft_prompt_renders_for_both_bases(base_dir):
    from orchestrator.steps.base import render_user_prompt

    for reference in (None, "examples/array/sums"):
        init = _init(base_dir, reference=reference)
        text = render_user_prompt(
            "spec_from_draft",
            {"problem_id": "my-task", "draft": "d", "base_spec": init.base_text, "reference": reference},
            prompts_dir=PROMPTS_DIR,
        )
        assert init.base_text.rstrip("\n") in text


def test_spec_review_marks_finds_mark_comments_only(tmp_path):
    from orchestrator.spec import spec_review_marks

    path = tmp_path / SPEC_FILENAME
    path.write_text(
        "problem_id: p\n"
        "title:\n"
        '  ru: "x"  # PROPOSED, REVIEW ME: from the legend\n'
        "legend_sketch: |  # SUGGESTION: shorter\n"
        "  text about a SUGGESTION without a comment\n",
        encoding="utf-8",
    )

    assert spec_review_marks(path) == [
        (3, 'ru: "x"  # PROPOSED, REVIEW ME: from the legend'),
        (4, "legend_sketch: |  # SUGGESTION: shorter"),
    ]
    assert spec_review_marks(tmp_path / "missing.yaml") == []
