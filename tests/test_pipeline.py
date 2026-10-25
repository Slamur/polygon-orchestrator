from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import pipeline
from orchestrator.model_router import ModelResponse

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

_RESPONSES = {
    "statement_draft": ModelResponse(
        status="confirmed",
        artifacts={
            "legend.tex": "tex",
            "input_format.tex": "tex",
            "output_format.tex": "tex",
            "notes.tex": "tex",
            "examples/example_1.txt": "1\n",
        },
        notes=[],
    ),
    "constraints_pick": ModelResponse(
        status="confirmed", artifacts={"constraints.yaml": "n_max: 100000"}, notes=[]
    ),
    "generators_and_script": ModelResponse(
        status="confirmed", artifacts={"test_script_groups": "<#-- fm --#>"}, notes=[]
    ),
    "solutions_draft": ModelResponse(
        status="confirmed", artifacts={"ok_cpp_draft.cpp": "int main(){}"}, notes=[]
    ),
    "checker_draft": ModelResponse(
        status="confirmed", artifacts={"checker.cpp": "int main(){}"}, notes=[]
    ),
}


def _fake_call_model(step, system_prompt, user_prompt, context_documents=None):
    return _RESPONSES[step]


@pytest.fixture
def spec_file(tmp_path):
    """Спек задачи `p1` в её каталоге: `<tmp>/problems/p1/spec.yaml`."""
    path = tmp_path / "problems" / "p1" / "spec.yaml"
    path.parent.mkdir(parents=True)
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", "problem_id: p1")
    path.write_text(content, encoding="utf-8")
    return path


def _run(problems_dir, **kwargs):
    return pipeline.run_pipeline(
        "p1",
        prompts_dir=PROMPTS_DIR,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
        **kwargs,
    )


def test_first_run_calls_model_for_every_step_in_order(spec_file, tmp_path):
    # Спек-фикстура заказывает стандартный чекер (checker.custom_needed:
    # false), поэтому checker_draft пропускается — он не менее часть
    # STEP_ORDER, но не вызывает модель.
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(problems_dir)

    assert result.ok
    assert [o.step_name for o in result.outcomes] == pipeline.STEP_ORDER
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    called_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: statuses[s] for s in called_steps} == {s: "confirmed" for s in called_steps}
    assert mock_call.call_count == len(called_steps)
    # порядок вызовов совпадает с STEP_ORDER (за вычетом пропущенного checker_draft)
    assert [c.args[0] for c in mock_call.call_args_list] == called_steps


def test_second_run_is_all_cache_hits_and_does_not_call_model(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(problems_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    # checker_draft остаётся skipped_optional на каждом прогоне, а не
    # cache_hit — он вообще не участвует в кэше при custom_needed=false.
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    cached_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: statuses[s] for s in cached_steps} == {
        s: pipeline.OUTCOME_CACHE_HIT for s in cached_steps
    }
    mock_call.assert_not_called()


def test_force_recalls_model_even_when_cached(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(problems_dir, force=True)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    # --force не отменяет "шаг вообще не применим для этой задачи" —
    # checker_draft всё равно skipped_optional при custom_needed=false.
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    forced_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: statuses[s] for s in forced_steps} == {s: "confirmed" for s in forced_steps}
    assert mock_call.call_count == len(forced_steps)


def test_uncertain_step_stops_pipeline_before_later_steps(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response) as mock_call:
        result = _run(problems_dir)

    assert not result.ok
    assert result.stopped_uncertain is True
    assert mock_call.call_count == 1
    assert len(result.outcomes) == 1
    assert result.outcomes[0].step_name == "statement_draft"
    assert result.outcomes[0].status == pipeline.OUTCOME_UNCERTAIN
    assert result.outcomes[0].notes == uncertain_response.notes
    # кроме самого спека в каталоге задачи на диск ничего не записано
    assert set(problems_dir.rglob("*")) == {spec_file.parent, spec_file}


def test_missing_spec_yields_spec_error_without_raising(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    result = pipeline.run_pipeline(
        "does-not-exist",
        prompts_dir=PROMPTS_DIR,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
    )
    assert not result.ok
    assert result.spec_error is not None
    assert result.outcomes == []


def test_solutions_draft_skipped_when_spec_has_no_solutions_section(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    content = (spec_file).read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    # вырезаем секцию solutions целиком (от "solutions:" до "checker:")
    start = next(i for i, line in enumerate(lines) if line.startswith("solutions:"))
    end = next(i for i, line in enumerate(lines) if line.startswith("checker:"))
    (spec_file).write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(problems_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["solutions_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    assert not (problems_dir / "p1" / "solutions").exists()


def test_checker_draft_skipped_when_custom_needed_is_false(spec_file, tmp_path):
    # Фикстура valid-spec.yaml заказывает стандартный чекер (ncmp) —
    # checker.custom_needed: false, черновик не заказан.
    problems_dir = tmp_path / "problems"

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(problems_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    assert not (problems_dir / "p1" / "checker.cpp").exists()


def test_checker_draft_runs_when_custom_needed_is_true(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    content = (spec_file).read_text(encoding="utf-8")
    content = content.replace("custom_needed: false", "custom_needed: true").replace(
        "custom_comparison_notes: null",
        'custom_comparison_notes: "Сравнивать числа с точностью 1e-6"',
    )
    (spec_file).write_text(content, encoding="utf-8")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(problems_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == "confirmed"
    assert (problems_dir / "p1" / "checker.cpp").exists()


def test_only_step_without_prior_constraints_yaml_reports_error_not_crash(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step="generators_and_script",
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )

    assert not result.ok
    assert result.stopped_uncertain is True
    assert result.outcomes[0].step_name == "generators_and_script"
    assert result.outcomes[0].status == "error"
    assert "constraints.yaml" in result.outcomes[0].detail
    mock_call.assert_not_called()


def test_only_step_picks_up_constraints_yaml_written_by_earlier_run(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        pipeline.run_pipeline(
            "p1",
            only_step="constraints_pick",
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step="generators_and_script",
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.ok
    assert result.outcomes[0].status == "confirmed"
    assert mock_call.call_count == 1
    assert mock_call.call_args.args[0] == "generators_and_script"


# --- compute_step_statuses ----------------------------------------------------


def test_compute_step_statuses_matches_cache_hits_after_full_run(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)

    statuses, spec_error = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir
    )
    assert spec_error is None
    by_name = {s.step_name: s.state for s in statuses}
    assert by_name["checker_draft"] == pipeline.STATUS_SKIPPED_OPTIONAL
    cached_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: by_name[s] for s in cached_steps} == {
        s: pipeline.STATUS_CACHE_HIT for s in cached_steps
    }


def test_compute_step_statuses_not_run_when_no_cache(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    statuses, spec_error = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir
    )
    assert spec_error is None
    assert statuses[0].state == pipeline.STATUS_NOT_RUN


def test_compute_step_statuses_stale_after_spec_edit(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)

    content = (spec_file).read_text(encoding="utf-8")
    content = content.replace("Есть граф городов и дорог", "Есть граф городов и дорог (отредактировано)")
    (spec_file).write_text(content, encoding="utf-8")

    statuses, _ = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir
    )
    by_name = {s.step_name: s for s in statuses}
    assert by_name["statement_draft"].state == pipeline.STATUS_STALE
    # шаги 2-4 не зависят от statement_draft — их кэш не тронут
    assert by_name["constraints_pick"].state == pipeline.STATUS_CACHE_HIT


def test_compute_step_statuses_uncertain_entry_never_reported_as_cache_hit():
    # run_generative_step (см. orchestrator/steps/base.py) сознательно не
    # пишет CacheEntry при status: uncertain — "not run" в compute_step_statuses
    # для этого случая корректен (см. test_uncertain_step_stops_pipeline_...
    # выше: следующий запуск всё равно позовёт модель заново). Эта проверка —
    # что status.py-логика вообще не выдаёт "cache hit" для status=uncertain,
    # даже если бы такая запись когда-нибудь появилась на диске (cache.py,
    # is_cache_valid, уже это гарантирует и покрыт тестами там).
    from orchestrator.cache import CacheEntry, UNCERTAIN_STATUS, is_cache_valid

    entry = CacheEntry("h", "p", "model", UNCERTAIN_STATUS, "2026-01-01T00:00:00Z")
    assert is_cache_valid(entry, "h", "p") is False


def test_compute_step_statuses_not_run_after_uncertain_run(spec_file, tmp_path):
    # run_generative_step не пишет CacheEntry при status: uncertain (см.
    # test_run_generative_step_uncertain_raises_and_writes_nothing в
    # tests/test_steps_base.py) — поэтому status для такого шага "not run",
    # а не "uncertain": кэша просто нет, и следующий запуск снова позовёт
    # модель, что и требуется (CLAUDE.md, "провал по недостатку данных
    # должен всплывать при каждом запуске").
    problems_dir = tmp_path / "problems"
    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response):
        _run(problems_dir)

    statuses, _ = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir
    )
    assert statuses[0].state == pipeline.STATUS_NOT_RUN


def test_compute_step_statuses_reports_spec_error(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    statuses, spec_error = pipeline.compute_step_statuses(
        "does-not-exist", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir
    )
    assert statuses == []
    assert spec_error is not None


def _break_reference(spec_file):
    # Общий референс — его получает каждый шаг, начиная с первого.
    path = spec_file
    content = path.read_text(encoding="utf-8")
    content = content.replace(
        "problem_id: p1\n", "problem_id: p1\nreferences:\n  - examples/no-such-task\n", 1
    )
    path.write_text(content, encoding="utf-8")


def test_missing_reference_stops_pipeline_before_calling_model(spec_file, tmp_path):
    _break_reference(spec_file)
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(problems_dir)

    assert not result.ok
    assert [(o.step_name, o.status) for o in result.outcomes] == [("statement_draft", "error")]
    assert "no-such-task" in result.outcomes[0].detail
    mock_call.assert_not_called()


def test_compute_step_statuses_blocked_by_missing_reference(spec_file, tmp_path):
    _break_reference(spec_file)

    statuses, error = pipeline.compute_step_statuses(
        "p1",
        prompts_dir=PROMPTS_DIR,
        problems_dir=tmp_path / "problems",
        templates_dir=TEMPLATES_DIR,
    )

    assert error is None
    by_step = {s.step_name: s for s in statuses}
    assert by_step["statement_draft"].state == pipeline.STATUS_BLOCKED
    assert "no-such-task" in by_step["statement_draft"].detail


# --- что именно изменилось, смена модели, llm accept ---------------------


def _statuses(problems_dir, prompts_dir=PROMPTS_DIR):
    statuses, spec_error = pipeline.compute_step_statuses(
        "p1",
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
    )
    assert spec_error is None
    return {s.step_name: s for s in statuses}


def _accept(problems_dir, **kwargs):
    statuses, spec_error = pipeline.accept_steps(
        "p1",
        prompts_dir=PROMPTS_DIR,
        problems_dir=problems_dir,
        templates_dir=TEMPLATES_DIR,
        **kwargs,
    )
    assert spec_error is None
    return {s.step_name: s for s in statuses}


def _edit_legend(spec_file):
    content = (spec_file).read_text(encoding="utf-8")
    content = content.replace("Есть граф городов и дорог", "Есть граф городов и дорог (отредактировано)")
    (spec_file).write_text(content, encoding="utf-8")


def test_stale_detail_names_changed_spec_field(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    _edit_legend(spec_file)

    detail = _statuses(problems_dir)["statement_draft"].detail
    assert detail == "spec (or upstream artifact) changed: spec: legend_sketch"


def test_stale_detail_names_changed_prompt_file(spec_file, tmp_path):
    import shutil

    problems_dir = tmp_path / "problems"
    prompts_dir = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, prompts_dir)
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        pipeline.run_pipeline(
            "p1",
            prompts_dir=prompts_dir,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
        )
    system_md = prompts_dir / "statement_draft" / "system.md"
    system_md.write_text(system_md.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")

    detail = _statuses(problems_dir, prompts_dir)["statement_draft"].detail
    assert detail == "prompt template changed: prompt: statement_draft/system.md"


def test_stale_detail_for_cache_entry_without_parts(spec_file, tmp_path):
    import json

    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    cache_file = problems_dir / "p1" / ".cache" / "statement_draft.json"
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    del raw["input_parts"], raw["prompt_parts"], raw["accepted"]
    cache_file.write_text(json.dumps(raw), encoding="utf-8")
    _edit_legend(spec_file)

    detail = _statuses(problems_dir)["statement_draft"].detail
    assert "spec (or upstream artifact) changed" in detail
    assert "predates per-part hashes" in detail


def test_model_change_invalidates_cache(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)

    with patch.dict(
        "orchestrator.model_router.STEP_TO_MODEL", {"statement_draft": "strong-model"}
    ):
        assert _statuses(problems_dir)["statement_draft"].detail == (
            "model changed (medium-model -> strong-model)"
        )
        with patch(
            "orchestrator.model_router.call_model", side_effect=_fake_call_model
        ) as mock_call:
            result = _run(problems_dir)

    assert [c.args[0] for c in mock_call.call_args_list] == ["statement_draft"]
    outcome = result.outcomes[0]
    assert outcome.detail == (
        "regenerated: model changed (medium-model -> strong-model); "
        "revising 5 current file(s)"
    )


def test_accept_makes_stale_step_a_cache_hit_without_calling_model(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    _edit_legend(spec_file)

    with patch("orchestrator.model_router.call_model") as mock_call:
        accepted = _accept(problems_dir)
        result = _run(problems_dir)

    mock_call.assert_not_called()
    assert accepted["statement_draft"].state == pipeline.STATUS_ACCEPTED
    assert "spec: legend_sketch" in accepted["statement_draft"].detail
    assert accepted["constraints_pick"].state == pipeline.STATUS_CACHE_HIT
    assert result.outcomes[0].status == pipeline.OUTCOME_CACHE_HIT

    from orchestrator.cache import load_cache_entry

    entry = load_cache_entry("p1", "statement_draft", problems_dir=problems_dir)
    assert entry.accepted is True
    assert entry.status == "confirmed"


def test_accept_after_hand_edited_constraints_yaml_accepts_downstream_steps(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    (problems_dir / "p1" / "constraints.yaml").write_text("n_max: 200000", encoding="utf-8")

    before = _statuses(problems_dir)
    assert before["generators_and_script"].detail == (
        "spec (or upstream artifact) changed: upstream: constraints.yaml"
    )

    accepted = _accept(problems_dir)
    assert accepted["generators_and_script"].state == pipeline.STATUS_ACCEPTED
    assert accepted["solutions_draft"].state == pipeline.STATUS_ACCEPTED
    after = _statuses(problems_dir)
    assert after["generators_and_script"].state == pipeline.STATUS_CACHE_HIT
    assert after["solutions_draft"].state == pipeline.STATUS_CACHE_HIT


def test_accept_only_step_leaves_other_steps_stale(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    (problems_dir / "p1" / "constraints.yaml").write_text("n_max: 200000", encoding="utf-8")

    accepted = _accept(problems_dir, only_step="solutions_draft")
    assert list(accepted) == ["solutions_draft"]
    after = _statuses(problems_dir)
    assert after["solutions_draft"].state == pipeline.STATUS_CACHE_HIT
    assert after["generators_and_script"].state == pipeline.STATUS_STALE


def test_accept_refuses_steps_never_generated(spec_file, tmp_path):
    problems_dir = tmp_path / "problems"
    accepted = _accept(problems_dir)
    assert accepted["statement_draft"].state == pipeline.STATUS_NOT_ACCEPTED
    assert not (problems_dir / "p1" / ".cache").exists()


def test_accept_refuses_step_with_missing_artifact(spec_file, tmp_path):
    import shutil

    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    shutil.rmtree(problems_dir / "p1" / "statement")

    accepted = _accept(problems_dir)
    assert accepted["statement_draft"].state == pipeline.STATUS_NOT_ACCEPTED
    assert "artifact is missing" in accepted["statement_draft"].detail


def test_accept_refuses_uncertain_step(spec_file, tmp_path):
    from orchestrator.cache import CacheEntry, UNCERTAIN_STATUS, load_cache_entry, save_cache_entry

    problems_dir = tmp_path / "problems"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(problems_dir)
    save_cache_entry(
        "p1",
        "statement_draft",
        CacheEntry("h", "p", "medium-model", UNCERTAIN_STATUS, "t"),
        problems_dir=problems_dir,
    )

    accepted = _accept(problems_dir)
    assert accepted["statement_draft"].state == pipeline.STATUS_NOT_ACCEPTED
    assert load_cache_entry("p1", "statement_draft", problems_dir=problems_dir).status == (
        UNCERTAIN_STATUS
    )
