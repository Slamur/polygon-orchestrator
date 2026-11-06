import py_compile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.checks.compile_check import compile_check
from orchestrator.model_router import ModelResponse
from orchestrator.references import SpecReferenceError
from orchestrator.spec import load_spec
from orchestrator.steps.base import StepUncertainError
from orchestrator.steps.solutions import STAGES
from orchestrator.steps.solutions import extra_context_documents as _extra_context_documents

MAIN = STAGES["main"]
MAIN_CODE = {"ok_cpp_main_draft.cpp": "int main() { /* the main one */ }"}


def _with_inputs(upstream, stage=MAIN, references=None, local_run=None):
    """`upstream_artifacts` as the pipeline gives them: plus what the stage loads itself."""
    if upstream is None:
        return None
    inputs = {"references": references or {}, "local_run": local_run}
    return {**upstream, stage.INPUTS_KEY: inputs}


def run_step(problem_id, spec, upstream, stage=MAIN, references=None, local_run=None, **kwargs):
    return stage.run_step(
        problem_id, spec, _with_inputs(upstream, stage, references, local_run), **kwargs
    )


def compute_input_hash(problem_id, spec, upstream, stage=MAIN, references=None):
    return stage.compute_input_hash(problem_id, spec, _with_inputs(upstream, stage, references))


def extra_context_documents(spec, **kwargs):
    return _extra_context_documents(MAIN.STEP_NAME, spec, **kwargs)


REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
SOLUTION_TEMPLATES = TEMPLATES_DIR / "solutions"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def spec():
    return load_spec(FIXTURES_DIR / "valid-spec.yaml")


# A path from the repository root — that's how a reference is written in the spec.
EXAMPLE_REF = "examples/array/prefix-sums"
EXAMPLE_DIR = REPO_ROOT / EXAMPLE_REF


def _write_reference(tmp_path: Path) -> Path:
    reference_dir = tmp_path / "old-task"
    solutions_dir = reference_dir / "solutions"
    solutions_dir.mkdir(parents=True)
    for name in [
        "ok_cpp_a_draft.cpp",
        "wa_cpp_b_draft.cpp",
        "ok_py_a_draft.py",
        "ok_java_a_draft.java",
    ]:
        (solutions_dir / name).write_text(f"// {name}", encoding="utf-8")
    return reference_dir


def _with_solutions(spec, languages, references):
    solutions = spec.solutions.model_copy(update={"languages": languages, "references": references})
    return spec.model_copy(update={"solutions": solutions})


def test_extra_context_documents_filters_solutions_by_language(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    documents = extra_context_documents(_with_solutions(spec, ["cpp"], [str(reference_dir)]))

    assert documents == [
        str(reference_dir / "solutions" / "ok_cpp_a_draft.cpp"),
        str(reference_dir / "solutions" / "wa_cpp_b_draft.cpp"),
    ]


def test_extra_context_documents_reads_language_after_combined_verdict(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)
    (reference_dir / "solutions" / "tl_ok_java_slowio_draft.java").write_text(
        "//", encoding="utf-8"
    )

    documents = extra_context_documents(_with_solutions(spec, ["java"], [str(reference_dir)]))

    assert documents == [
        str(reference_dir / "solutions" / "ok_java_a_draft.java"),
        str(reference_dir / "solutions" / "tl_ok_java_slowio_draft.java"),
    ]


def test_extra_context_documents_accepts_language_aliases(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    documents = extra_context_documents(_with_solutions(spec, ["Python"], [str(reference_dir)]))

    assert documents == [str(reference_dir / "solutions" / "ok_py_a_draft.py")]


def test_extra_context_documents_without_matching_solutions(spec, tmp_path):
    reference_dir = _write_reference(tmp_path)

    # A per-step reference that gave the step no files is a spec error.
    with pytest.raises(SpecReferenceError, match="solutions.references"):
        extra_context_documents(_with_solutions(spec, ["kotlin"], [str(reference_dir)]))
    # A shared reference without suitable files for this step is simply skipped.
    common_only = _with_solutions(spec, ["kotlin"], None).model_copy(
        update={"references": [str(reference_dir)]}
    )
    assert extra_context_documents(common_only) is None
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    assert extra_context_documents(spec_no_solutions) is None


def test_extra_context_documents_without_references_attaches_language_templates(spec):
    documents = extra_context_documents(
        _with_solutions(spec, ["Python", "cpp", "kotlin"], None), templates_dir=TEMPLATES_DIR
    )

    # There's no skeleton for kotlin — the language just stays without one.
    assert documents == [
        str(SOLUTION_TEMPLATES / "ok.cpp"),
        str(SOLUTION_TEMPLATES / "ok.py"),
        str(SOLUTION_TEMPLATES / "tl_ok_slowio.py"),
    ]


def test_extra_context_documents_attaches_all_templates_of_a_language(spec):
    documents = extra_context_documents(
        _with_solutions(spec, ["java"], None), templates_dir=TEMPLATES_DIR
    )

    assert documents == [
        str(SOLUTION_TEMPLATES / "ok.java"),
        str(SOLUTION_TEMPLATES / "tl_ok_slowio.java"),
    ]


def test_extra_context_documents_attaches_template_for_language_without_ok_reference(
    spec, tmp_path
):
    reference_dir = tmp_path / "old-task"
    (reference_dir / "solutions").mkdir(parents=True)
    for name in ["wa_cpp_b_draft.cpp", "ok_java_a_draft.java"]:
        (reference_dir / "solutions" / name).write_text(f"// {name}", encoding="utf-8")

    documents = extra_context_documents(
        _with_solutions(spec, ["cpp", "java"], [str(reference_dir)]), templates_dir=TEMPLATES_DIR
    )

    # java has an ok sample — no skeletons needed; cpp has only wa — they're needed.
    assert documents == [
        str(SOLUTION_TEMPLATES / "ok.cpp"),
        str(reference_dir / "solutions" / "ok_java_a_draft.java"),
        str(reference_dir / "solutions" / "wa_cpp_b_draft.cpp"),
    ]


def test_run_step_attaches_language_template_under_templates_key(spec, tmp_path):
    response = ModelResponse(
        status="confirmed", artifacts={"ok_cpp_main_draft.cpp": "int main(){}"}, notes=[]
    )
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    for compiled in result.compile_results.values():
        if compiled.binary_path:
            compiled.binary_path.unlink(missing_ok=True)

    context_documents = mock_call.call_args.kwargs["context_documents"]
    assert context_documents["solutions/ok.cpp"] == (SOLUTION_TEMPLATES / "ok.cpp").read_text(
        encoding="utf-8"
    )


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
def test_cpp_solution_template_compiles():
    result = compile_check(SOLUTION_TEMPLATES / "ok.cpp", [TEMPLATES_DIR])
    if result.binary_path:
        result.binary_path.unlink(missing_ok=True)
    assert result.success, result.stderr


@pytest.mark.parametrize("name", ["ok.py", "tl_ok_slowio.py"])
def test_python_solution_templates_compile(name, tmp_path):
    py_compile.compile(
        str(SOLUTION_TEMPLATES / name), cfile=str(tmp_path / f"{name}c"), doraise=True
    )


def test_run_step_attaches_reference_solutions_of_requested_languages(spec, tmp_path):
    spec = spec.model_copy(update={"references": [EXAMPLE_REF]})
    response = ModelResponse(
        status="confirmed", artifacts={"ok_cpp_main_draft.cpp": "int main(){}"}, notes=[]
    )
    upstream = {"constraints.yaml": "n_max: 100000"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    for compiled in result.compile_results.values():
        if compiled.binary_path:
            compiled.binary_path.unlink(missing_ok=True)

    # The fixture orders only cpp — solutions in other languages aren't attached.
    context_documents = mock_call.call_args.kwargs["context_documents"]
    attached = [
        d for d in context_documents if d.startswith("examples/array/prefix-sums/solutions/")
    ]
    expected = sorted(
        f"examples/array/prefix-sums/solutions/{p.name}"
        for p in (EXAMPLE_DIR / "solutions").glob("*_cpp_*")
    )
    assert expected
    assert attached == expected
    assert "examples/array/prefix-sums/spec.yaml" in context_documents


def test_missing_solutions_section_raises(spec, tmp_path):
    spec_no_solutions = spec.model_copy(update={"solutions": None})
    upstream = {"constraints.yaml": "n_max: 100000"}
    with pytest.raises(ValueError, match="solutions"):
        run_step(
            "valid-spec",
            spec_no_solutions,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
        )


def test_missing_constraints_upstream_artifact_raises(spec, tmp_path):
    with pytest.raises(ValueError, match="constraints.yaml"):
        run_step("valid-spec", spec, None, prompts_dir=PROMPTS_DIR, problems_dir=tmp_path)


def test_confirmed_writes_solution_files(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"ok_cpp_main_draft.cpp": "int main(){}"},
        notes=[],
    )
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        result = run_step(
            "valid-spec",
            spec,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"
    written_path = tmp_path / "valid-spec" / "solutions" / "ok_cpp_main_draft.cpp"
    assert written_path.exists()

    user_prompt = mock_call.call_args.args[2]
    assert "Dijkstra" in user_prompt
    assert "O((N + K) log N)" in user_prompt


def test_uncertain_raises_and_writes_nothing(spec, tmp_path):
    response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[
            {
                "field": "solutions.algorithm_hint",
                "kind": "uncertain",
                "explanation": "too vague",
            }
        ],
    )
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        with pytest.raises(StepUncertainError):
            run_step(
                "valid-spec",
                spec,
                upstream,
                prompts_dir=PROMPTS_DIR,
                problems_dir=tmp_path,
                templates_dir=TEMPLATES_DIR,
            )

    assert not (tmp_path / "valid-spec").exists()


def test_renders_without_crashing_when_known_wrong_approaches_is_null(spec, tmp_path):
    spec_bare = spec.model_copy(
        update={"solutions": spec.solutions.model_copy(update={"known_wrong_approaches": None})}
    )
    response = ModelResponse(
        status="confirmed", artifacts={"ok_cpp_main_draft.cpp": "int main(){}"}, notes=[]
    )
    upstream = {"constraints.yaml": "time_limit_seconds: 2"}
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec_bare,
            upstream,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.status == "confirmed"


def _run_and_get_user_prompt(spec, tmp_path, upstream, **stage_kwargs):
    response = ModelResponse(status="confirmed", artifacts={}, notes=[])
    with patch("orchestrator.model_router.call_model", return_value=response) as mock_call:
        run_step(
            "valid-spec",
            spec,
            upstream,
            **stage_kwargs,
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    return mock_call.call_args.args[2]


def test_user_prompt_includes_statement_io(spec, tmp_path):
    user_prompt = _run_and_get_user_prompt(spec, tmp_path, {"constraints.yaml": "n_max: 100000"})

    statement = spec.statement_draft
    assert statement.legend_sketch.strip() in user_prompt
    assert statement.formal_input_sketch.strip() in user_prompt
    assert statement.formal_output_sketch.strip() in user_prompt
    assert statement.indexing in user_prompt
    for ex in statement.sample_examples:
        assert ex.input.strip() in user_prompt
        assert ex.output.strip() in user_prompt


def test_user_prompt_includes_constraints_yaml_without_limits(spec, tmp_path):
    constraints_yaml = (
        "# comment\n"
        "variables:\n  n: {min: 1, max: 200000}\n"
        "limits:\n  time_limit_seconds: 7\n  memory_limit_mb: 768\n  rationale: |\n\n    why\n"
        "test_groups:\n  - name: g1\n"
    )
    user_prompt = _run_and_get_user_prompt(spec, tmp_path, {"constraints.yaml": constraints_yaml})

    assert "# comment\nvariables:\n  n: {min: 1, max: 200000}\n" in user_prompt
    assert "test_groups:\n  - name: g1" in user_prompt
    assert "time_limit_seconds" not in user_prompt
    assert "768" not in user_prompt
    assert "why" not in user_prompt


def test_user_prompt_drops_root_level_limits(spec, tmp_path):
    constraints_yaml = "n_max: 100000\ntime_limit_seconds: 7\nmemory_limit_mb: 768\n"
    user_prompt = _run_and_get_user_prompt(spec, tmp_path, {"constraints.yaml": constraints_yaml})

    assert "n_max: 100000" in user_prompt
    assert "time_limit_seconds" not in user_prompt
    assert "memory_limit_mb" not in user_prompt


def test_compute_input_hash_ignores_limits(spec):
    def constraints(n_max: int, tl: int) -> dict[str, str]:
        return {"constraints.yaml": f"n_max: {n_max}\nlimits:\n  time_limit_seconds: {tl}\n"}

    h1 = compute_input_hash("valid-spec", spec, constraints(200000, 1))
    h2 = compute_input_hash("valid-spec", spec, constraints(200000, 3))
    h3 = compute_input_hash("valid-spec", spec, constraints(100000, 1))

    assert h1 == h2
    assert h1 != h3


def test_user_prompt_marks_missing_sample_output(spec, tmp_path):
    examples = [
        ex.model_copy(update={"output": None}) for ex in spec.statement_draft.sample_examples
    ]
    spec_no_output = spec.model_copy(
        update={
            "statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})
        }
    )
    user_prompt = _run_and_get_user_prompt(
        spec_no_output, tmp_path, {"constraints.yaml": "n_max: 1"}
    )

    assert "Output: the author didn't give it" in user_prompt


@pytest.mark.parametrize(
    "field, value",
    [
        ("formal_input_sketch", "One line: N."),
        ("formal_output_sketch", "Print YES or NO."),
        ("indexing", "0-indexed"),
        ("legend_sketch", "Another legend."),
    ],
)
def test_compute_input_hash_changes_when_statement_io_changes(spec, field, value):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    spec_changed = spec.model_copy(
        update={"statement_draft": spec.statement_draft.model_copy(update={field: value})}
    )
    h2 = compute_input_hash("valid-spec", spec_changed, upstream)

    assert h1 != h2


def test_compute_input_hash_changes_when_sample_output_changes(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    h1 = compute_input_hash("valid-spec", spec, upstream)

    examples = [
        ex.model_copy(update={"output": "-1\n"}) for ex in spec.statement_draft.sample_examples
    ]
    spec_changed = spec.model_copy(
        update={
            "statement_draft": spec.statement_draft.model_copy(update={"sample_examples": examples})
        }
    )
    h2 = compute_input_hash("valid-spec", spec_changed, upstream)

    assert h1 != h2


def test_user_prompt_includes_alternative_approaches(spec, tmp_path):
    approach = "ok (cpp): a floating-point root with an integer correction"
    spec_alt = spec.model_copy(
        update={
            "solutions": spec.solutions.model_copy(update={"alternative_approaches": [approach]})
        }
    )
    upstream = {"constraints.yaml": "n_max: 1"}
    user_prompt = _run_and_get_user_prompt(
        spec_alt, tmp_path, upstream, stage=STAGES["ok"], references=MAIN_CODE
    )
    assert approach in user_prompt
    assert MAIN_CODE["ok_cpp_main_draft.cpp"] in user_prompt
    # The main stage doesn't see the other approaches.
    assert approach not in _run_and_get_user_prompt(spec_alt, tmp_path, upstream)


def test_compute_input_hash_changes_when_alternative_approaches_change(spec):
    upstream = {"constraints.yaml": "n_max: 100000"}
    ok = {"stage": STAGES["ok"], "references": MAIN_CODE}
    h1 = compute_input_hash("valid-spec", spec, upstream, **ok)
    main_hash = compute_input_hash("valid-spec", spec, upstream)

    spec_alt = spec.model_copy(
        update={
            "solutions": spec.solutions.model_copy(
                update={"alternative_approaches": ["ok: another approach"]}
            )
        }
    )

    assert compute_input_hash("valid-spec", spec_alt, upstream, **ok) != h1
    assert compute_input_hash("valid-spec", spec_alt, upstream) == main_hash


def test_solution_name_with_solutions_prefix_is_not_nested(spec, tmp_path):
    response = ModelResponse(
        status="confirmed", artifacts={"solutions/ok_py_main_draft.py": "print(1)\n"}, notes=[]
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        run_step(
            "valid-spec",
            spec,
            {"constraints.yaml": "n_max: 1"},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    solutions_dir = tmp_path / "valid-spec" / "solutions"
    assert (solutions_dir / "ok_py_main_draft.py").exists()
    assert not (solutions_dir / "solutions").exists()


def test_solution_name_with_foreign_path_is_set_aside_and_the_rest_applied(spec, tmp_path):
    response = ModelResponse(
        status="confirmed",
        artifacts={"ok_py_main_draft.py": "print(1)\n", "generators/ok_cpp_x_draft.cpp": ""},
        notes=[],
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            {"constraints.yaml": "n_max: 1"},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )

    solutions_dir = tmp_path / "valid-spec" / "solutions"
    assert [p.name for p in solutions_dir.iterdir()] == ["ok_py_main_draft.py"]
    [(filename, reason)] = result.conflicts
    assert filename == "generators/ok_cpp_x_draft.cpp"
    assert "not a plain file name" in reason


def _interactive_with_solutions():
    plain = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    interactive = load_spec(FIXTURES_DIR / "interactive-spec.yaml")
    return interactive.model_copy(update={"solutions": plain.solutions})


def test_interactive_problem_gets_protocol_and_rules():
    from orchestrator.steps.base import statement_io_section

    spec = _interactive_with_solutions()
    documents = extra_context_documents(spec, templates_dir=TEMPLATES_DIR)
    assert documents[0] == str(TEMPLATES_DIR / "tutorials" / "interactive.md")
    section = statement_io_section(spec, include_output_and_legend=True)
    assert section["interaction_sketch"] == spec.statement_draft.interaction_sketch
    # The validator and generators don't need the protocol: they work with the test.
    assert "interaction_sketch" not in statement_io_section(spec, include_output_and_legend=False)


def test_plain_problem_gets_no_protocol_or_rules():
    from orchestrator.steps.base import statement_io_section

    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    documents = extra_context_documents(spec, templates_dir=TEMPLATES_DIR) or []
    assert not any("interactive.md" in path for path in documents)
    assert "interaction_sketch" not in statement_io_section(spec, include_output_and_legend=True)


# --- the stages ----------------------------------------------------------------


def _write_solutions(problem_dir: Path, names: list[str]) -> None:
    solutions = problem_dir / "solutions"
    solutions.mkdir(parents=True, exist_ok=True)
    for name in names:
        (solutions / name).write_text(f"// {name}\n", encoding="utf-8")


def _owned(problem_dir: Path) -> dict[str, list[str]]:
    return {key: sorted(stage.owned(problem_dir)) for key, stage in STAGES.items()}


def test_stages_own_solutions_by_name(tmp_path):
    _write_solutions(
        tmp_path,
        [
            "ok_cpp_alt_draft.cpp",
            "ok_cpp_main_draft.cpp",
            "ok_py_main_draft.py",
            "tl_ok_java_slowio_draft.java",
            "ml_cpp_big_draft.cpp",
            "wa_py_bound_draft.py",
            "re_cpp_stack_draft.cpp",
            "notes.txt",
        ],
    )
    assert _owned(tmp_path) == {
        "main": ["ok_cpp_main_draft.cpp"],
        "ok": ["ok_cpp_alt_draft.cpp", "ok_py_main_draft.py"],
        "slow": ["ml_cpp_big_draft.cpp", "tl_ok_java_slowio_draft.java"],
        "wrong": ["re_cpp_stack_draft.cpp", "wa_py_bound_draft.py"],
    }


def test_main_stage_owns_main_correct_without_the_main_description(tmp_path):
    # Solutions written before the stages: Main correct is the main one.
    _write_solutions(tmp_path, ["ok_cpp_prefs_draft.cpp", "ok_py_prefs_draft.py"])
    assert _owned(tmp_path)["main"] == ["ok_cpp_prefs_draft.cpp"]
    assert _owned(tmp_path)["ok"] == ["ok_py_prefs_draft.py"]


def test_stage_response_with_another_stage_file_is_not_applied(spec, tmp_path):
    response = ModelResponse(
        status="confirmed", artifacts={"wa_cpp_off_draft.cpp": "int main(){}"}, notes=[]
    )
    with patch("orchestrator.model_router.call_model", return_value=response):
        result = run_step(
            "valid-spec",
            spec,
            {"constraints.yaml": "n_max: 1"},
            prompts_dir=PROMPTS_DIR,
            problems_dir=tmp_path,
            templates_dir=TEMPLATES_DIR,
        )
    assert "not a solution of this stage" in result.not_applied_reason
    assert not (tmp_path / "valid-spec" / "solutions" / "wa_cpp_off_draft.cpp").exists()


def _solutions_update(spec, **update):
    return spec.model_copy(update={"solutions": spec.solutions.model_copy(update=update)})


def test_skip_detail_by_what_the_spec_orders(spec):
    # The fixture: cpp only, wanted ok/wa/tl, one tl approach.
    assert {key: stage.skip_detail(spec) is None for key, stage in STAGES.items()} == {
        "main": True,
        "ok": False,
        "slow": True,
        "wrong": True,
    }
    two_languages = _solutions_update(spec, languages=["cpp", "java"])
    assert STAGES["ok"].skip_detail(two_languages) is None
    no_wrong = _solutions_update(spec, wanted_verdicts=["ok", "tl"])
    assert STAGES["wrong"].skip_detail(no_wrong) is not None
    no_slow = _solutions_update(spec, wanted_verdicts=["ok"], known_wrong_approaches=[])
    assert STAGES["slow"].skip_detail(no_slow) is not None
    # Java may need a tl_ok solution with slow input/output.
    assert STAGES["slow"].skip_detail(_solutions_update(no_slow, languages=["java"])) is None


def test_slow_and_wrong_stages_get_only_their_approaches(spec, tmp_path):
    spec = _solutions_update(
        spec,
        known_wrong_approaches=["tl: a quadratic loop", "wa (py): an off-by-one bound"],
    )
    upstream = {"constraints.yaml": "n_max: 1"}
    slow = _run_and_get_user_prompt(
        spec, tmp_path, upstream, stage=STAGES["slow"], references=MAIN_CODE
    )
    wrong = _run_and_get_user_prompt(
        spec, tmp_path, upstream, stage=STAGES["wrong"], references=MAIN_CODE
    )
    assert "a quadratic loop" in slow and "off-by-one" not in slow
    assert "off-by-one" in wrong and "a quadratic loop" not in wrong
    assert "wanted_verdicts: ['tl']" in slow
    assert "wanted_verdicts: ['wa']" in wrong


def test_hash_of_a_stage_changes_with_the_code_it_builds_on(spec):
    upstream = {"constraints.yaml": "n_max: 1"}
    wrong = STAGES["wrong"]
    before = compute_input_hash("valid-spec", spec, upstream, stage=wrong, references=MAIN_CODE)
    edited = {"ok_cpp_main_draft.cpp": "int main() { /* fixed */ }"}
    after = compute_input_hash("valid-spec", spec, upstream, stage=wrong, references=edited)
    assert before != after


def test_load_inputs_needs_the_main_solution(spec, tmp_path):
    with pytest.raises(ValueError, match="solutions_main"):
        STAGES["ok"].load_inputs("valid-spec", spec, problems_dir=tmp_path)


def test_load_inputs_of_wrong_stage_gives_all_ok_solutions(spec, tmp_path):
    _write_solutions(
        tmp_path / "valid-spec",
        ["ok_cpp_main_draft.cpp", "ok_py_port_draft.py", "wa_cpp_off_draft.cpp"],
    )
    inputs = STAGES["wrong"].load_inputs("valid-spec", spec, problems_dir=tmp_path)
    assert sorted(inputs["references"]) == ["ok_cpp_main_draft.cpp", "ok_py_port_draft.py"]
    assert inputs["local_run"] is None


_LOCAL_RUN = {
    "report": "llm_reports/run_main.md",
    "failures": [
        {
            "name": "ok_cpp_main_draft.cpp",
            "status": "unexpected",
            "error": None,
            "unexpected": [
                {
                    "test": "test 3",
                    "verdict": "RE",
                    "command": "gen_rand -n=100000",
                    "comment": "exit code -11",
                }
            ],
        }
    ],
    "comment": "the recursion is too deep — make it iterative",
}


def test_failed_local_run_forces_a_revision_with_its_report(spec, tmp_path):
    upstream = {"constraints.yaml": "n_max: 1"}
    with_report = _with_inputs(upstream, local_run=_LOCAL_RUN)
    reason = MAIN.forced_rerun_reason("valid-spec", spec, with_report)
    assert "run_main" in reason and "ok_cpp_main_draft.cpp" in reason
    assert MAIN.forced_rerun_reason("valid-spec", spec, _with_inputs(upstream)) is None
    # The report isn't part of the input: after the revision the cache is valid again.
    assert compute_input_hash("valid-spec", spec, upstream) == MAIN.compute_input_hash(
        "valid-spec", spec, with_report
    )

    prompt = _run_and_get_user_prompt(spec, tmp_path, upstream, local_run=_LOCAL_RUN)
    assert "## The local run of your solutions failed" in prompt
    assert "test 3: RE — generated by `gen_rand -n=100000`" in prompt
    assert "the recursion is too deep — make it iterative" in prompt
