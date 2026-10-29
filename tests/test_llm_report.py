from pathlib import Path

from orchestrator.llm_report import (
    note_counts,
    report_path,
    write_spec_from_draft_report,
    write_step_report,
)
from orchestrator.pipeline import StepOutcome
from orchestrator.spec_from_draft import SpecFromDraftResult


def _note(kind, field, explanation):
    return {"kind": kind, "field": field, "explanation": explanation}


def _problem(tmp_path: Path) -> Path:
    problem_dir = tmp_path / "problems" / "my-task"
    (problem_dir / "generators").mkdir(parents=True)
    return problem_dir


def _report(problem_dir: Path, outcome: StepOutcome) -> str:
    path = write_step_report(
        problem_dir, outcome, status_label="proposed", command_line="problems/my-task llm generate"
    )
    assert path == report_path(problem_dir, outcome.step_name)
    return path.read_text(encoding="utf-8")


def test_notes_are_grouped_by_kind_most_important_first(tmp_path):
    problem_dir = _problem(tmp_path)
    outcome = StepOutcome(
        "tests_plan",
        "proposed",
        notes=[
            _note("changed", "test_script", "added a block"),
            _note("suggestion", "gen_x.cpp", "an idea"),
            _note("proposed", "gen_y.cpp", "first line\nsecond line"),
            _note("ignored", "zzz", "unknown file"),
        ],
        ran=True,
    )

    report = _report(problem_dir, outcome)

    assert report.startswith("# tests_plan: proposed\n")
    assert "`orchestrator problems/my-task llm generate`" in report
    positions = [
        report.index(title)
        for title in (
            "## Proposed — review (1)",
            "## Suggestions beyond the spec (1)",
            "## Changed (1)",
            "## Other notes (1)",
        )
    ]
    assert positions == sorted(positions)
    assert "- **gen_y.cpp** — first line\n  second line" in report
    assert "- [ignored] **zzz** — unknown file" in report
    assert "Uncertain" not in report


def test_diff_only_for_files_that_existed_before(tmp_path):
    problem_dir = _problem(tmp_path)
    backup_dir = problem_dir / ".llm_backup" / "20260101T000000Z"
    (backup_dir / "generators").mkdir(parents=True)
    (backup_dir / "generators" / "gen_a.cpp").write_text("int a = 1;\n", encoding="utf-8")
    changed = problem_dir / "generators" / "gen_a.cpp"
    changed.write_text("int a = 2;\n", encoding="utf-8")
    new = problem_dir / "generators" / "gen_b.cpp"
    new.write_text("int b;\n", encoding="utf-8")
    same = problem_dir / "test_script"
    same.write_text("x\n", encoding="utf-8")
    outcome = StepOutcome(
        "tests_plan",
        "confirmed",
        ran=True,
        artifact_paths={"gen_a.cpp": changed, "gen_b.cpp": new, "test_script": same},
        created=[new],
        backup_dir=backup_dir,
    )

    report = _report(problem_dir, outcome)

    assert "- changed: [generators/gen_a.cpp](<../generators/gen_a.cpp>)" in report
    assert "- new: [generators/gen_b.cpp](<../generators/gen_b.cpp>)" in report
    assert "test_script" not in report
    assert "- previous versions: `.llm_backup/20260101T000000Z`" in report
    assert "<summary><code>generators/gen_a.cpp</code></summary>" in report
    assert "-int a = 1;\n+int a = 2;" in report
    assert "gen_b.cpp</code>" not in report


def test_renamed_file_is_diffed_against_its_old_name(tmp_path):
    problem_dir = _problem(tmp_path)
    (problem_dir / "solutions").mkdir()
    backup_dir = problem_dir / ".llm_backup" / "t"
    (backup_dir / "solutions").mkdir(parents=True)
    (backup_dir / "solutions" / "ok.cpp").write_text("old\n", encoding="utf-8")
    new_path = problem_dir / "solutions" / "wa.cpp"
    new_path.write_text("new\n", encoding="utf-8")
    outcome = StepOutcome(
        "solutions_draft",
        "confirmed",
        ran=True,
        artifact_paths={"wa.cpp": new_path},
        renamed=[(problem_dir / "solutions" / "ok.cpp", new_path, "now wrong")],
        backup_dir=backup_dir,
    )

    report = _report(problem_dir, outcome)

    assert "- renamed: `solutions/ok.cpp` -> [solutions/wa.cpp](<../solutions/wa.cpp>) — now wrong" in report
    assert "<code>solutions/ok.cpp -> solutions/wa.cpp</code>" in report
    assert "-old\n+new" in report


def test_compile_errors_and_fences_longer_than_backticks_inside(tmp_path):
    problem_dir = _problem(tmp_path)
    outcome = StepOutcome(
        "solutions_draft",
        "confirmed",
        ran=True,
        compile_errors={"ok.cpp": "error: ```bad```"},
    )

    report = _report(problem_dir, outcome)

    assert "## Compile errors" in report
    assert "````text\nerror: ```bad```\n````" in report


def test_note_counts():
    notes = [_note("changed", "a", ""), _note("proposed", "b", ""), _note("changed", "c", "")]
    assert note_counts(notes) == "1 proposed, 2 changed"
    assert note_counts([]) == ""


def test_spec_from_draft_report(tmp_path):
    problem_dir = _problem(tmp_path)
    spec = problem_dir / "authoring" / "spec.yaml"
    result = SpecFromDraftResult(
        status="proposed",
        notes=[_note("proposed", "constraints.intended_complexity", "guessed")],
        path=spec,
        validation_errors=["bad field"],
    )

    path = write_spec_from_draft_report(
        problem_dir,
        result,
        status_label="proposed",
        command_line="problems/my-task init --draft idea",
        draft_path=Path("drafts/idea.md"),
    )

    report = path.read_text(encoding="utf-8")
    assert path == problem_dir / "llm_reports" / "spec_from_draft.md"
    assert "[authoring/spec.yaml](<../authoring/spec.yaml>)" in report
    assert "- bad field" in report
    assert "- **constraints.intended_complexity** — guessed" in report
