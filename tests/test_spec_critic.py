"""The critic of the spec of `init --draft` (`orchestrator/spec_critic.py`).
The model is replaced (`model_router.call_model`) — there are no real calls."""

from pathlib import Path

import pytest
import yaml

from orchestrator import cli, model_router
from orchestrator.llm_client import LLMCallError
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import AUTHORING_DIRNAME
from orchestrator.spec import SPEC_FILENAME, SpecInit
from orchestrator.spec_critic import (
    AUTHOR,
    BEFORE_CRITIC_FILENAME,
    FOR_REVIEW,
    KEPT,
    MODEL,
    NOT_CHANGED,
    REJECTED,
    REMOVED,
    REPEATED,
    REVISED,
    UNRESOLVED,
    Finding,
    author_values,
    classify,
    critique_spec,
    parse_critique,
    remap_path,
    remove_list_item,
    strip_review_marks,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

SPEC = """\
problem_id: my-task
title: Sums
tests:
  groups:
    - name: samples
      points: 0
    - name: big  # SUGGESTION: stress the limits
      points: 50
      note: max n
    - name: small
      points: 50
solutions:
  algorithm_hint: prefix sums  # PROPOSED, REVIEW ME: from the draft's idea
  known_wrong_approaches:  # keep: the author's comment
    - quadratic loop
    - overflow in int  # SUGGESTION: a common mistake
"""

DRAFT = "A synthetic draft: given an array, find the sum.\n"


def _finding(field, severity="should", action="remove", **kwargs):
    return Finding(field=field, severity=severity, action=action, why="w", **kwargs)


def _critique(*items) -> ModelResponse:
    return ModelResponse(
        status="proposed" if items else "confirmed",
        artifacts={"critique.yaml": yaml.safe_dump(list(items), sort_keys=False)},
    )


def _item(field, severity="should", action="remove", why="not needed", **kwargs):
    return {"field": field, "severity": severity, "action": action, "why": why, **kwargs}


class _FakeModel:
    """The critic gets the `critiques` in turn (then — no findings), the generator's
    revision — the `revisions` in turn."""

    def __init__(self, critiques=(), revisions=()):
        self.critiques = list(critiques)
        self.revisions = list(revisions)
        self.calls: list[dict] = []

    def __call__(self, step, system_prompt, user_prompt, context_documents=None):
        self.calls.append(
            dict(step=step, system=system_prompt, user=user_prompt, documents=context_documents)
        )
        if step == "spec_critic":
            return self.critiques.pop(0) if self.critiques else _critique()
        return self.revisions.pop(0)

    def steps(self):
        return [call["step"] for call in self.calls]


@pytest.fixture
def fake_model(monkeypatch):
    def install(**kwargs) -> _FakeModel:
        fake = _FakeModel(**kwargs)
        monkeypatch.setattr(model_router, "call_model", fake)
        return fake

    return install


def _run(tmp_path, spec=SPEC, **kwargs):
    init = SpecInit(problem_id="my-task", path=tmp_path / SPEC_FILENAME, base_text=spec)
    (tmp_path / "examples").mkdir(exist_ok=True)
    return critique_spec(
        init,
        DRAFT,
        spec,
        prompts_dir=PROMPTS_DIR,
        templates_dir=TEMPLATES_DIR,
        examples_dir=tmp_path / "examples",
        **kwargs,
    )


# --- the spec as the critic and the router see it ---------------------------


def test_strip_review_marks_keeps_other_comments():
    stripped = strip_review_marks(SPEC + "# SUGGESTION: a line of its own\n")
    assert "SUGGESTION" not in stripped and "PROPOSED" not in stripped
    assert "    - name: big\n" in stripped
    assert "algorithm_hint: prefix sums\n" in stripped
    assert "# keep: the author's comment" in stripped
    assert not stripped.endswith("a line of its own\n")
    assert yaml.safe_load(stripped) == yaml.safe_load(SPEC)


@pytest.mark.parametrize(
    "field, owner, mark, normalized",
    [
        ("tests.groups[0]", AUTHOR, None, "tests.groups[0]"),
        ("tests.groups[1]", MODEL, "SUGGESTION", "tests.groups[1]"),
        # The mark is on the item's line — its fields are the model's too.
        ("tests.groups[1].note", MODEL, "SUGGESTION", "tests.groups[1].note"),
        ("tests.groups.1.points", MODEL, "SUGGESTION", "tests.groups[1].points"),
        ("solutions.algorithm_hint", MODEL, "PROPOSED", "solutions.algorithm_hint"),
        # A section that only partly holds proposed values is the author's.
        ("solutions", AUTHOR, None, "solutions"),
        (
            "solutions.known_wrong_approaches[0]",
            AUTHOR,
            None,
            "solutions.known_wrong_approaches[0]",
        ),
    ],
)
def test_classify_by_marks(field, owner, mark, normalized):
    finding = _finding(field)
    classify(finding, SPEC)
    assert (finding.owner, finding.mark, finding.field) == (owner, mark, normalized)
    assert finding.outcome == FOR_REVIEW


@pytest.mark.parametrize("field", ["tests.groups[7]", "tests.nothing", "title.x", ""])
def test_classify_unresolved(field):
    finding = _finding(field)
    classify(finding, SPEC)
    assert finding.outcome == UNRESOLVED and finding.owner is None


def test_remove_list_item_cuts_the_whole_block():
    result = remove_list_item(SPEC, ("tests", "groups", 1))
    data = yaml.safe_load(result)
    assert [g["name"] for g in data["tests"]["groups"]] == ["samples", "small"]
    assert "SUGGESTION: stress" not in result
    assert "# keep: the author's comment" in result


def test_remove_list_item_refuses_what_it_cant_cut():
    # The last item: the list would become null instead of [].
    single = "ideas:\n  - one  # SUGGESTION: x\n"
    assert remove_list_item(single, ("ideas", 0)) is None
    # A flow sequence: there's no item line to cut.
    assert remove_list_item("ideas: [a, b]  # SUGGESTION: x\n", ("ideas", 0)) is None
    assert remove_list_item(SPEC, ("tests", "groups")) is None


def test_remap_path():
    removed = ("tests", "groups", 1)
    assert remap_path(("tests", "groups", 2, "name"), removed) == ("tests", "groups", 1, "name")
    assert remap_path(("tests", "groups", 1, "note"), removed) is None
    assert remap_path(("tests", "groups", 0), removed) == ("tests", "groups", 0)
    assert remap_path(("solutions", "algorithm_hint"), removed) == ("solutions", "algorithm_hint")


def test_author_values_ignore_marked_values_only():
    marked_edit = SPEC.replace("points: 50\n      note: max n", "points: 30")
    marked_edit = marked_edit.replace("prefix sums  # PROPOSED", "a loop  # PROPOSED")
    assert author_values(marked_edit) == author_values(SPEC)
    assert author_values(SPEC.replace("quadratic loop", "a cubic loop")) != author_values(SPEC)
    # A dropped mark makes the value the author's — the revision may not do it.
    assert author_values(SPEC.replace("  # SUGGESTION: stress the limits", "")) != author_values(
        SPEC
    )


def test_parse_critique_skips_invalid_items():
    text = yaml.safe_dump(
        [
            _item("tests.groups[1]"),
            _item("tests.groups[2]", action="add"),
            _item("tests.groups[2]", severity="critical"),
            {"severity": "must", "action": "remove"},
            "just text",
        ]
    )
    items, notes = parse_critique(text)
    assert [i["field"] for i in items] == ["tests.groups[1]"]
    assert len(notes) == 4 and all(n["kind"] == "uncertain" for n in notes)
    assert parse_critique("[]") == ([], [])
    assert parse_critique(None)[0] == [] and parse_critique("a: [")[1]


# --- routing ----------------------------------------------------------------


def test_critic_sees_the_spec_without_marks_and_draft(tmp_path, fake_model):
    fake = fake_model()
    outcome = _run(tmp_path)

    assert fake.steps() == ["spec_critic"]
    call = fake.calls[0]
    assert "SUGGESTION" not in call["user"] and "PROPOSED" not in call["user"]
    assert DRAFT.strip() not in call["user"]
    assert "spec_template.yaml" not in call["documents"]
    assert "docs/SPEC_FORMAT.md" in call["documents"]
    assert "tutorials/tests.md" in call["documents"]
    assert "tutorials/interactive.md" not in call["documents"]
    assert outcome.findings == [] and not outcome.changed


def test_author_values_and_nits_go_to_the_report_only(tmp_path, fake_model):
    fake = fake_model(
        critiques=[
            _critique(
                _item("solutions.known_wrong_approaches[0]", severity="must"),
                _item("solutions.algorithm_hint", severity="nit", action="simplify"),
                _item("no.such.field"),
            )
        ]
    )
    outcome = _run(tmp_path)

    assert fake.steps() == ["spec_critic"]
    assert [f.outcome for f in outcome.findings] == [FOR_REVIEW, FOR_REVIEW, UNRESOLVED]
    assert outcome.spec_text == SPEC


def test_suggestion_items_are_removed_without_the_model(tmp_path, fake_model):
    fake = fake_model(
        critiques=[
            _critique(
                _item("tests.groups[1]"), _item("solutions.known_wrong_approaches[1]", "must")
            )
        ]
    )
    notes = [
        {"field": "tests.groups[1]", "kind": "suggestion", "explanation": "stress"},
        {"field": "tests.groups[2].name", "kind": "proposed", "explanation": "x"},
    ]
    outcome = _run(tmp_path, generator_notes=notes)

    assert fake.steps() == ["spec_critic"]
    assert [f.outcome for f in outcome.findings] == [REMOVED, REMOVED]
    data = yaml.safe_load(outcome.spec_text)
    assert [g["name"] for g in data["tests"]["groups"]] == ["samples", "small"]
    assert data["solutions"]["known_wrong_approaches"] == ["quadratic loop"]
    # The note of the removed item is dropped, the later item's path moved up.
    assert notes == [{"field": "tests.groups[1].name", "kind": "proposed", "explanation": "x"}]


def _revision(spec_text, notes=(), status="proposed"):
    return ModelResponse(
        status=status,
        artifacts={} if spec_text is None else {SPEC_FILENAME: spec_text},
        notes=list(notes),
        unchanged=[SPEC_FILENAME] if spec_text is None else [],
    )


def test_proposed_values_go_to_one_revision_round(tmp_path, fake_model):
    revised = SPEC.replace("prefix sums  # PROPOSED", "a running sum  # PROPOSED")
    fake = fake_model(
        critiques=[
            _critique(
                _item("solutions.algorithm_hint", action="simplify", proposal="a running sum"),
                # A PROPOSED value isn't cut mechanically even for `remove`.
                _item("tests.groups[1].note", severity="must"),
                _item("tests.groups[1]", severity="should", action="question"),
            )
        ],
        revisions=[
            _revision(
                revised,
                notes=[
                    {"field": "tests.groups[1]", "kind": "uncertain", "explanation": "the draft"},
                    {"field": "x", "kind": "changed", "explanation": "y"},
                ],
            )
        ],
    )
    outcome = _run(tmp_path)

    assert fake.steps() == ["spec_critic", "spec_from_draft"]
    prompt = fake.calls[1]["user"]
    assert "Reviewer's findings" in prompt and DRAFT.strip() in prompt
    assert "solutions.algorithm_hint" in prompt and "SUGGESTION: stress" in prompt
    by_field = {f.field: f for f in outcome.findings}
    assert by_field["solutions.algorithm_hint"].outcome == REVISED
    assert by_field["tests.groups[1].note"].outcome == NOT_CHANGED
    assert (by_field["tests.groups[1]"].outcome, by_field["tests.groups[1]"].detail) == (
        KEPT,
        "the draft",
    )
    assert yaml.safe_load(outcome.spec_text)["solutions"]["algorithm_hint"] == "a running sum"
    assert [n["field"] for n in outcome.notes] == ["x"]


def test_revision_touching_author_values_is_rejected(tmp_path, fake_model):
    revised = SPEC.replace("prefix sums", "a running sum").replace("quadratic", "cubic")
    fake_model(
        critiques=[_critique(_item("solutions.algorithm_hint", action="simplify"))],
        revisions=[_revision(revised)],
    )
    outcome = _run(tmp_path)

    assert outcome.findings[0].outcome == REJECTED
    assert outcome.spec_text == SPEC
    assert any("revision rejected" in n["explanation"] for n in outcome.notes)


def test_second_round_only_with_new_findings(tmp_path, fake_model):
    same = _item("tests.groups[1].note", action="simplify")
    fake = fake_model(
        critiques=[_critique(same), _critique(same)],
        revisions=[_revision(None, status="confirmed")],
    )
    outcome = _run(tmp_path, rounds=2)

    assert fake.steps() == ["spec_critic", "spec_from_draft", "spec_critic"]
    assert "Findings of the previous round" in fake.calls[2]["user"]
    assert [f.outcome for f in outcome.findings] == [NOT_CHANGED, REPEATED]
    assert outcome.rounds == 2


def test_one_round_by_default(tmp_path, fake_model):
    fake = fake_model(
        critiques=[_critique(_item("tests.groups[1].note", action="simplify"))],
        revisions=[_revision(None, status="confirmed")],
    )
    _run(tmp_path)
    assert fake.steps() == ["spec_critic", "spec_from_draft"]


# --- init --draft -----------------------------------------------------------


@pytest.fixture
def base_dir(tmp_path):
    (tmp_path / "examples").mkdir()
    (tmp_path / "problems").mkdir()
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "my-idea.md").write_text(DRAFT, encoding="utf-8")
    return tmp_path


def _marked_valid_spec() -> str:
    """The valid spec fixture with the first `specific_test_ideas` item marked as
    the model's suggestion."""
    lines = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip() == "specific_test_ideas:")
    lines[index + 1] += "  # SUGGESTION: a minimal test"
    return "\n".join(lines).replace("problem_id: valid-spec", "problem_id: my-task") + "\n"


class _CliModel:
    def __init__(self, spec_text, critique):
        self.spec_text, self.critique = spec_text, critique
        self.steps: list[str] = []

    def __call__(self, step, system_prompt, user_prompt, context_documents=None):
        self.steps.append(step)
        if step == "spec_critic":
            if isinstance(self.critique, Exception):
                raise self.critique
            return self.critique
        return ModelResponse(status="proposed", artifacts={SPEC_FILENAME: self.spec_text})


def _cli(base_dir, *extra):
    return cli.main(
        ["--base-dir", str(base_dir), "problems/my-task", "init", "--draft", "my-idea", *extra]
    )


def test_cli_init_draft_applies_the_critique(base_dir, monkeypatch, capsys):
    fake = _CliModel(
        _marked_valid_spec(),
        _critique(
            _item("generation.specific_test_ideas[0]"),
            _item("statement_draft.legend_sketch", action="simplify", proposal="shorter"),
        ),
    )
    monkeypatch.setattr(model_router, "call_model", fake)

    assert _cli(base_dir) == 0

    problem_dir = base_dir / "problems" / "my-task"
    spec = problem_dir / AUTHORING_DIRNAME / SPEC_FILENAME
    assert fake.steps == ["spec_from_draft", "spec_critic"]
    assert "SUGGESTION: a minimal test" not in spec.read_text(encoding="utf-8")
    backups = list((problem_dir / ".llm_backup").glob(f"*/{AUTHORING_DIRNAME}/*"))
    assert [b.name for b in backups] == [BEFORE_CRITIC_FILENAME]
    report = (problem_dir / "llm_reports" / "spec_from_draft.md").read_text(encoding="utf-8")
    assert "## Critic: 2 finding(s)" in report
    assert "### Your values — for your review (1)" in report
    assert "→ **removed**" in report and "The critic's changes:" in report
    assert "critic: 2 finding(s)" in capsys.readouterr().out


def test_cli_init_draft_no_critic(base_dir, monkeypatch):
    fake = _CliModel(_marked_valid_spec(), _critique(_item("generation.specific_test_ideas[0]")))
    monkeypatch.setattr(model_router, "call_model", fake)

    assert _cli(base_dir, "--no-critic") == 0
    assert fake.steps == ["spec_from_draft"]


def test_cli_init_draft_failed_critic_keeps_the_spec(base_dir, monkeypatch, capsys):
    fake = _CliModel(_marked_valid_spec(), LLMCallError("killed"))
    monkeypatch.setattr(model_router, "call_model", fake)

    assert _cli(base_dir) == 1

    spec = base_dir / "problems" / "my-task" / AUTHORING_DIRNAME / SPEC_FILENAME
    assert "SUGGESTION: a minimal test" in spec.read_text(encoding="utf-8")
    assert "the critic call failed" in capsys.readouterr().out
