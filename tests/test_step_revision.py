"""Режим доработки генеративного шага (`orchestrator/steps/base.py`,
`StepRevision`): модель получает текущие файлы шага и изменения входа, а
ответ раскладывается на изменённые / `unchanged` / `removed` / `renamed`."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import pipeline
from orchestrator.cache import (
    changed_input_values,
    compute_input_values,
    load_cache_entry,
    load_pending_response,
)
from orchestrator.model_router import ModelResponse
from orchestrator.steps.base import (
    CONFLICTS_REPORT,
    INVALID_NAMES_SUBDIR,
    LLM_BACKUP_DIRNAME,
    LLM_CONFLICTS_DIRNAME,
    REVISE_PROMPT,
    StepRevision,
    run_generative_step,
)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def prompts_dir(tmp_path):
    directory = tmp_path / "prompts"
    step_dir = directory / "solutions_draft"
    step_dir.mkdir(parents=True)
    (step_dir / "system.md").write_text("system rules", encoding="utf-8")
    (step_dir / "user.md.j2").write_text("context: {{ problem_id }}", encoding="utf-8")
    shutil.copyfile(PROMPTS_DIR / REVISE_PROMPT, directory / REVISE_PROMPT)
    return directory


@pytest.fixture
def problem_dir(tmp_path):
    directory = tmp_path / "problems" / "p1"
    (directory / "solutions").mkdir(parents=True)
    return directory


def _write_current(problem_dir: Path, files: dict[str, str]) -> dict[str, Path]:
    paths = {}
    for name, content in files.items():
        path = problem_dir / "solutions" / name
        path.write_text(content, encoding="utf-8")
        paths[name] = path
    return paths


def _run(problem_dir, prompts_dir, response, revision=None, **kwargs):
    def resolve(name: str) -> Path:
        name = name.removeprefix("solutions/")
        if "/" in name:
            raise ValueError(f"bad name {name}")
        return problem_dir / "solutions" / name

    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_generative_step(
            step_name="solutions_draft",
            problem_id="p1",
            context={"problem_id": "p1"},
            input_hash="hash",
            input_values=kwargs.pop("input_values", {"spec: algorithm_hint": "new"}),
            resolve_artifact_path=resolve,
            revision=revision,
            prompts_dir=prompts_dir,
            problems_dir=problem_dir.parent,
            templates_dir=TEMPLATES_DIR,
            **kwargs,
        )
    return result, mock_call


def _response(**kwargs) -> ModelResponse:
    return ModelResponse(status="confirmed", **kwargs)


# --- промпт -----------------------------------------------------------------


def test_revision_prompt_contains_current_files_and_input_changes(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"ok_py_main.py": "print(1)\n"})
    revision = StepRevision(
        current_paths=paths,
        previous_input_values={"spec: algorithm_hint": "old", "spec: languages": ["py"]},
        other_changes=["prompt: solutions_draft/system.md"],
    )

    _, mock_call = _run(
        problem_dir,
        prompts_dir,
        _response(unchanged=["ok_py_main.py"]),
        revision,
        input_values={"spec: algorithm_hint": "new", "spec: languages": ["py"]},
    )

    user_prompt = mock_call.call_args.args[2]
    assert user_prompt.startswith("context: p1\n\n## Режим доработки")
    assert "#### `ok_py_main.py`\n\n````\nprint(1)\n````" in user_prompt
    assert "#### `spec: algorithm_hint`" in user_prompt
    assert "Было:\n````\nold\n````" in user_prompt
    assert "Стало:\n````\nnew\n````" in user_prompt
    assert "spec: languages" not in user_prompt
    assert "prompt: solutions_draft/system.md" in user_prompt


def test_revision_prompt_says_changes_are_unknown_without_previous_values(
    problem_dir, prompts_dir
):
    paths = _write_current(problem_dir, {"ok_py_main.py": "print(1)\n"})

    _, mock_call = _run(
        problem_dir,
        prompts_dir,
        _response(unchanged=["ok_py_main.py"]),
        StepRevision(current_paths=paths),
    )

    assert "Неизвестно: прошлый запуск не сохранил свой вход" in mock_call.call_args.args[2]


def test_fresh_run_does_not_render_revision_prompt(problem_dir, prompts_dir):
    _, mock_call = _run(problem_dir, prompts_dir, _response(artifacts={"ok_py_a.py": "x"}))

    assert mock_call.call_args.args[2] == "context: p1"


# --- применение ответа --------------------------------------------------------


def test_revision_applies_modified_unchanged_removed_renamed_and_new(problem_dir, prompts_dir):
    paths = _write_current(
        problem_dir,
        {
            "ok_py_main.py": "print(1)\n",
            "ok_py_keep.py": "print(2)\n",
            "wa_py_old.py": "print(3)\n",
            "wa_py_slow.py": "print(4)\n",
            "ok_py_alt.py": "print(5)\n",
        },
    )
    response = _response(
        artifacts={
            "ok_py_main.py": "print(10)\n",
            "solutions/tl_py_slow.py": "print(40)\n",
            "ok_py_new.py": "print(6)\n",
        },
        unchanged=["ok_py_keep.py"],
        removed=[{"filename": "wa_py_old.py", "reason": "approach dropped from spec"}],
        renamed=[
            {"from": "wa_py_slow.py", "to": "tl_py_slow.py", "reason": "verdict changed"},
            {"from": "solutions/ok_py_alt.py", "to": "ok_py_other.py", "reason": "renamed"},
        ],
    )

    result, _ = _run(problem_dir, prompts_dir, response, StepRevision(current_paths=paths))

    solutions = problem_dir / "solutions"
    assert sorted(path.name for path in solutions.iterdir()) == [
        "ok_py_keep.py",
        "ok_py_main.py",
        "ok_py_new.py",
        "ok_py_other.py",
        "tl_py_slow.py",
        "wa_py_old.py.removed",
    ]
    assert (solutions / "ok_py_main.py").read_text() == "print(10)\n"
    assert (solutions / "ok_py_keep.py").read_text() == "print(2)\n"
    assert (solutions / "tl_py_slow.py").read_text() == "print(40)\n"
    assert (solutions / "ok_py_other.py").read_text() == "print(5)\n"
    assert (solutions / "wa_py_old.py.removed").read_text() == "print(3)\n"
    assert result.revised
    assert result.removed == [(solutions / "wa_py_old.py.removed", "approach dropped from spec")]
    assert [(old.name, new.name) for old, new, _ in result.renamed] == [
        ("wa_py_slow.py", "tl_py_slow.py"),
        ("ok_py_alt.py", "ok_py_other.py"),
    ]

    # Прежние версии изменённых и переименованных файлов — в бэкапе.
    assert result.backup_dir is not None
    assert result.backup_dir.parent == problem_dir / LLM_BACKUP_DIRNAME
    backup = result.backup_dir / "solutions"
    assert sorted(path.name for path in backup.iterdir()) == [
        "ok_py_alt.py",
        "ok_py_main.py",
        "wa_py_slow.py",
    ]
    assert (backup / "ok_py_main.py").read_text() == "print(1)\n"
    assert (backup / "wa_py_slow.py").read_text() == "print(4)\n"


def test_unchanged_only_revision_writes_nothing_and_makes_no_backup(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"ok_py_main.py": "print(1)\n"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(unchanged=["ok_py_main.py"]),
        StepRevision(current_paths=paths),
    )

    assert result.artifact_paths == {}
    assert result.backup_dir is None
    assert load_cache_entry("p1", "solutions_draft", problems_dir=problem_dir.parent) is not None


def test_existing_removed_mark_is_backed_up_before_being_replaced(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"wa_py_old.py": "new\n"})
    (problem_dir / "solutions" / "wa_py_old.py.removed").write_text("older\n")

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(removed=[{"filename": "wa_py_old.py", "reason": "r"}]),
        StepRevision(current_paths=paths),
    )

    assert (problem_dir / "solutions" / "wa_py_old.py.removed").read_text() == "new\n"
    assert (result.backup_dir / "solutions" / "wa_py_old.py.removed").read_text() == "older\n"


def test_fresh_run_backs_up_overwritten_files(problem_dir, prompts_dir):
    _write_current(problem_dir, {"ok_py_main.py": "print(1)\n", "ok_py_same.py": "same\n"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(artifacts={"ok_py_main.py": "print(2)\n", "ok_py_same.py": "same\n"}),
    )

    assert (problem_dir / "solutions" / "ok_py_main.py").read_text() == "print(2)\n"
    assert sorted(path.name for path in (result.backup_dir / "solutions").iterdir()) == [
        "ok_py_main.py"
    ]


def test_fresh_run_ignores_revision_fields_with_a_note(problem_dir, prompts_dir):
    _write_current(problem_dir, {"ok_py_main.py": "print(1)\n"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(
            artifacts={"ok_py_a.py": "x"},
            removed=[{"filename": "ok_py_main.py", "reason": "r"}],
        ),
    )

    assert (problem_dir / "solutions" / "ok_py_main.py").exists()
    assert [note["kind"] for note in result.notes] == ["ignored"]


def test_validate_artifacts_gets_final_set_of_files(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b", "c.py": "c"})
    seen = {}

    _run(
        problem_dir,
        prompts_dir,
        _response(
            artifacts={"d.py": "d"},
            unchanged=["a.py"],
            removed=[{"filename": "b.py", "reason": "r"}],
            renamed=[{"from": "c.py", "to": "e.py", "reason": "r"}],
        ),
        StepRevision(current_paths=paths),
        validate_artifacts=seen.update,
    )

    assert seen == {"a.py": "a", "e.py": "c", "d.py": "d"}


# --- конфликты в ответе --------------------------------------------------------


def _solution_files(problem_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((problem_dir / "solutions").iterdir())
    }


def _conflict_files(result) -> dict[str, str]:
    """Отложенные версии модели (без отчёта) — путь в каталоге конфликтов -> содержимое."""
    assert result.conflicts_dir is not None
    return {
        path.relative_to(result.conflicts_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(result.conflicts_dir.rglob("*"))
        if path.is_file() and path.name != CONFLICTS_REPORT
    }


def test_current_file_missing_from_response_is_kept(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(artifacts={"a.py": "x"}),
        StepRevision(current_paths=paths),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"a.py": "x", "b.py": "b"}
    assert result.conflicts == [] and result.conflicts_dir is None
    assert {"field": "b.py", "kind": "kept", "explanation": "not mentioned in the response — kept as is"} in result.notes


def test_unknown_names_in_response_are_ignored(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(
            unchanged=["a.py", "zzz.py"],
            removed=[{"filename": "yyy.py", "reason": "r"}],
            renamed=[{"from": "xxx.py", "to": "c.py", "reason": "r"}],
        ),
        StepRevision(current_paths=paths),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"a.py": "a"}
    assert result.conflicts == []
    ignored = {note["field"] for note in result.notes if note["kind"] == "ignored"}
    assert ignored == {"zzz.py", "yyy.py", "xxx.py"}


def test_file_in_several_places_is_a_conflict_and_the_rest_is_applied(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(artifacts={"a.py": "x", "b.py": "y"}, unchanged=["a.py"]),
        StepRevision(current_paths=paths),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"a.py": "a", "b.py": "y"}
    [(filename, reason)] = result.conflicts
    assert filename == "a.py"
    assert "in several places of the response (artifacts, unchanged)" in reason
    assert _conflict_files(result) == {"solutions/a.py": "x"}
    report = (result.conflicts_dir / CONFLICTS_REPORT).read_text(encoding="utf-8")
    assert "`a.py`" in report and "model version: `solutions/a.py`" in report
    assert "llm accept --step solutions_draft" in report
    entry = load_cache_entry("p1", "solutions_draft", problems_dir=problem_dir.parent)
    assert entry.conflicts == result.conflicts_dir.relative_to(problem_dir).as_posix()
    assert entry.conflicts.startswith(LLM_CONFLICTS_DIRNAME + "/solutions_draft-")
    # Ответ применён частично — сохранённый ответ больше не нужен.
    assert load_pending_response("p1", "solutions_draft", problems_dir=problem_dir.parent) is None


def test_rename_to_taken_name_is_a_conflict(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(
            artifacts={"a.py": "new b"},
            renamed=[{"from": "b.py", "to": "a.py", "reason": "r"}],
        ),
        StepRevision(current_paths=paths),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"a.py": "a", "b.py": "b"}
    reasons = dict(result.conflicts)
    assert "the new name is already taken — 'b.py' is not renamed" in reasons["a.py"]
    # Новая версия под занятым именем отложена, а не записана поверх a.py.
    assert _conflict_files(result) == {"solutions/a.py": "new b"}


def test_renamed_file_also_in_artifacts_is_a_conflict(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(
            artifacts={"b.py": "x"},
            renamed=[{"from": "b.py", "to": "c.py", "reason": "r"}],
            unchanged=["a.py"],
        ),
        StepRevision(current_paths=paths),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"a.py": "a", "b.py": "b"}
    assert [filename for filename, _ in result.conflicts] == ["b.py", "c.py"]
    assert _conflict_files(result) == {"solutions/b.py": "x"}


def test_invalid_name_is_a_conflict_and_set_aside(problem_dir, prompts_dir):
    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(artifacts={"ok.py": "ok", "sub/x.py": "x"}),
        partial_apply=True,
    )

    assert _solution_files(problem_dir) == {"ok.py": "ok"}
    assert result.conflicts == [("sub/x.py", "bad name sub/x.py")]
    assert _conflict_files(result) == {f"{INVALID_NAMES_SUBDIR}/sub_x.py": "x"}


def test_step_applied_only_as_a_whole_applies_nothing_on_conflict(problem_dir, prompts_dir):
    paths = _write_current(problem_dir, {"a.py": "a", "b.py": "b"})

    result, _ = _run(
        problem_dir,
        prompts_dir,
        _response(
            artifacts={"a.py": "x", "b.py": "y"},
            unchanged=["a.py"],
        ),
        StepRevision(current_paths=paths),
    )

    assert _solution_files(problem_dir) == {"a.py": "a", "b.py": "b"}
    assert _conflict_files(result) == {"solutions/a.py": "x", "solutions/b.py": "y"}
    report = (result.conflicts_dir / CONFLICTS_REPORT).read_text(encoding="utf-8")
    assert "Nothing of the response is applied: the files of this step are applied only all together" in report
    # Не применено ничего — ответ остаётся для `llm apply`.
    assert load_pending_response("p1", "solutions_draft", problems_dir=problem_dir.parent) is not None


# --- кэш ---------------------------------------------------------------------


def test_input_values_are_saved_in_cache(problem_dir, prompts_dir):
    _run(
        problem_dir,
        prompts_dir,
        _response(artifacts={"ok_py_a.py": "x"}),
        input_values={"spec: algorithm_hint": "hint"},
    )

    entry = load_cache_entry("p1", "solutions_draft", problems_dir=problem_dir.parent)
    assert entry.input_values == {"spec: algorithm_hint": "hint"}
    assert set(entry.input_parts) == {"spec: algorithm_hint"}


def test_changed_input_values():
    old = compute_input_values({"a": 1, "b": {"x": 1, "y": 2}, "gone": 0})
    new = compute_input_values({"a": 1, "b": {"x": 1, "y": 3}, "added": None})

    assert changed_input_values(old, new) == [
        ("spec: b.y", 2, 3),
        ("spec: added", None, None),
        ("spec: gone", 0, None),
    ]
    assert changed_input_values(None, new) is None


# --- пайплайн ----------------------------------------------------------------


_PIPELINE_RESPONSES = {
    "statement_draft": ModelResponse(
        status="confirmed",
        artifacts={
            "legend.tex": "tex",
            "input_format.tex": "tex",
            "output_format.tex": "tex",
            "notes.tex": "tex",
            "examples/example_1.txt": "1\n",
        },
    ),
    "constraints_pick": ModelResponse(
        status="confirmed",
        artifacts={
            "constraints.yaml": "n_max: 100000\nlimits:\n  time_limit_seconds: 1\n  memory_limit_mb: 256\n"
        },
    ),
    "generators_and_script": ModelResponse(
        status="confirmed", artifacts={"test_script_groups": "<#-- fm --#>"}
    ),
    "solutions_draft": ModelResponse(
        status="confirmed", artifacts={"ok_cpp_draft.cpp": "int main(){}"}
    ),
}


@pytest.fixture
def spec_file(tmp_path):
    path = tmp_path / "problems" / "p1" / "authoring" / "spec.yaml"
    path.parent.mkdir(parents=True)
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    path.write_text(content.replace("problem_id: valid-spec", "problem_id: p1"), encoding="utf-8")
    return path


def _run_pipeline(problems_dir, responses=_PIPELINE_RESPONSES, **kwargs):
    with patch(
        "orchestrator.model_router.call_model",
        side_effect=lambda step, *args, **kw: responses[step],
    ) as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
            **kwargs,
        )
    return result, mock_call


def _user_prompt(mock_call, step: str) -> str:
    return next(c.args[2] for c in mock_call.call_args_list if c.args[0] == step)


def _edit_legend(spec_file: Path) -> None:
    text = spec_file.read_text(encoding="utf-8")
    text = text.replace("Есть граф городов и дорог", "Есть граф городов и дорог (Edited.)")
    spec_file.write_text(text, encoding="utf-8")


def test_pipeline_revises_stale_step_with_spec_diff(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)
    _edit_legend(spec_file)
    responses = dict(_PIPELINE_RESPONSES)
    responses["statement_draft"] = ModelResponse(
        status="confirmed",
        artifacts={"legend.tex": "edited tex"},
        unchanged=[
            "input_format.tex",
            "output_format.tex",
            "notes.tex",
            "examples/example_1.txt",
        ],
    )

    result, mock_call = _run_pipeline(problems_dir, responses)

    assert result.ok
    # Легенда — вход statement_draft, generators_and_script и solutions_draft.
    assert [c.args[0] for c in mock_call.call_args_list] == [
        "statement_draft",
        "generators_and_script",
        "solutions_draft",
    ]
    assert result.outcomes[0].detail.endswith("; revising 4 current file(s)")
    prompt = _user_prompt(mock_call, "statement_draft")
    assert "#### `spec: legend_sketch`" in prompt
    assert "Edited." in prompt
    assert "#### `legend.tex`" in prompt
    assert (problems_dir / "p1" / "statement" / "legend.tex").read_text() == "edited tex"


def test_pipeline_fresh_ignores_cache_and_current_files(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)

    result, mock_call = _run_pipeline(problems_dir, fresh=True, only_step="solutions_draft")

    assert [c.args[0] for c in mock_call.call_args_list] == ["solutions_draft"]
    assert result.outcomes[0].detail == "from scratch (--fresh)"
    assert "Режим доработки" not in _user_prompt(mock_call, "solutions_draft")


def test_pipeline_force_revises_current_files(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)

    result, mock_call = _run_pipeline(problems_dir, force=True, only_step="solutions_draft")

    assert result.outcomes[0].detail == "forced (--force); revising 1 current file(s)"
    prompt = _user_prompt(mock_call, "solutions_draft")
    assert "Вход шага не менялся" in prompt
    assert "#### `ok_cpp_draft.cpp`" in prompt


def test_pipeline_does_not_show_removed_marks_as_current_files(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)
    (problems_dir / "p1" / "solutions" / "wa_cpp_old.cpp.removed").write_text("old")

    _, mock_call = _run_pipeline(problems_dir, force=True, only_step="solutions_draft")

    assert "wa_cpp_old" not in _user_prompt(mock_call, "solutions_draft")


def test_solutions_dir_with_only_removed_marks_is_missing_artifact(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)
    solutions = problems_dir / "p1" / "solutions"
    (solutions / "ok_cpp_draft.cpp").rename(solutions / "ok_cpp_draft.cpp.removed")

    statuses, _ = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir, templates_dir=TEMPLATES_DIR
    )

    assert {s.step_name: s.state for s in statuses}["solutions_draft"] == pipeline.STATUS_STALE


# --- текущие файлы шагов ------------------------------------------------------


def _spec():
    from orchestrator.spec import load_spec

    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_statement_current_artifacts_skip_name_and_example_inputs(tmp_path):
    from orchestrator.steps import statement_draft

    statement = tmp_path / "p1" / "statement"
    for name in ("legend.tex", "notes.tex", "name.txt"):
        _touch(statement / name)
    for name in ("example_10.txt", "example_2.txt", "example_1.txt", "example_3.txt.removed"):
        _touch(statement / "examples" / name)

    current = statement_draft.current_artifacts("p1", _spec(), problems_dir=tmp_path)

    # Input примеров пишет оркестратор из спека — модели они не показываются.
    assert list(current) == ["legend.tex", "notes.tex"]


def test_statement_current_artifacts_sort_dialogues_numerically(tmp_path):
    from orchestrator.steps import statement_draft

    examples = tmp_path / "p1" / "statement" / "examples"
    for number in (10, 2, 1):
        for name in statement_draft.dialogue_filenames(number):
            _touch(examples / name)

    current = statement_draft.current_artifacts("p1", _spec(), problems_dir=tmp_path)

    assert list(current) == [
        f"examples/{name}"
        for number in (1, 2, 10)
        for name in statement_draft.dialogue_filenames(number)
    ]


def test_generators_current_artifacts(tmp_path):
    from orchestrator.steps import generators_and_script

    problem = tmp_path / "p1"
    _touch(problem / "generators" / "gen_b.cpp")
    _touch(problem / "generators" / "gen_a.cpp")
    _touch(problem / "generators" / "gen_old.cpp.removed")
    _touch(problem / "generators" / "notes.txt")
    _touch(problem / "test_script")

    current = generators_and_script.current_artifacts("p1", _spec(), problems_dir=tmp_path)

    assert current == {
        "gen_a.cpp": problem / "generators" / "gen_a.cpp",
        "gen_b.cpp": problem / "generators" / "gen_b.cpp",
        "test_script": problem / "test_script",
    }


def test_single_file_steps_current_artifacts(tmp_path):
    from orchestrator.steps import checker_draft, constraints_pick

    problem = tmp_path / "p1"
    assert constraints_pick.current_artifacts("p1", _spec(), problems_dir=tmp_path) == {}
    _touch(problem / "validator.cpp")
    _touch(problem / "checker.cpp")

    assert list(constraints_pick.current_artifacts("p1", _spec(), problems_dir=tmp_path)) == [
        "validator.cpp"
    ]
    assert list(checker_draft.current_artifacts("p1", _spec(), problems_dir=tmp_path)) == [
        "checker.cpp"
    ]


# --- CLI ---------------------------------------------------------------------


def test_cli_fresh_flag_and_revision_report(spec_file, tmp_path, capsys):
    from orchestrator import cli

    problems_dir = tmp_path / "problems"
    _run_pipeline(problems_dir)
    responses = dict(_PIPELINE_RESPONSES)
    responses["solutions_draft"] = ModelResponse(
        status="confirmed",
        renamed=[{"from": "ok_cpp_draft.cpp", "to": "wa_cpp_draft.cpp", "reason": "now wrong"}],
    )
    base = [
        "--prompts-dir", str(PROMPTS_DIR),
        "--base-dir", str(tmp_path),
        "--templates-dir", str(TEMPLATES_DIR),
        "problems/p1", "llm", "generate", "--step", "solutions_draft",
    ]

    with patch(
        "orchestrator.model_router.call_model",
        side_effect=lambda step, *args, **kw: responses[step],
    ):
        assert cli.main(base + ["--force"]) == 0
    out = capsys.readouterr().out
    assert "renamed: " in out and "wa_cpp_draft.cpp — now wrong" in out
    assert "previous versions of overwritten files: " in out

    with patch(
        "orchestrator.model_router.call_model",
        side_effect=lambda step, *args, **kw: _PIPELINE_RESPONSES[step],
    ) as mock_call:
        assert cli.main(base + ["--fresh"]) == 0
    assert "Режим доработки" not in mock_call.call_args.args[2]
    assert "from scratch (--fresh)" in capsys.readouterr().out


def test_revise_prompt_is_part_of_every_step_prompt_hash(prompts_dir):
    from orchestrator.cache import compute_prompt_hash, compute_prompt_parts

    before = compute_prompt_hash("solutions_draft", prompts_dir=prompts_dir, templates_dir=TEMPLATES_DIR)
    (prompts_dir / REVISE_PROMPT).write_text("edited", encoding="utf-8")
    after = compute_prompt_hash("solutions_draft", prompts_dir=prompts_dir, templates_dir=TEMPLATES_DIR)

    assert before != after
    assert f"prompt: {REVISE_PROMPT}" in compute_prompt_parts(
        "solutions_draft", prompts_dir=prompts_dir, templates_dir=TEMPLATES_DIR
    )
