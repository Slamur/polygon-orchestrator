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
    "statement_draft": ModelResponse(status="confirmed", artifacts={"statement.tex": "tex"}, notes=[]),
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
def specs_dir(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", "problem_id: p1")
    (specs / "p1.yaml").write_text(content, encoding="utf-8")
    return specs


def _run(specs_dir, outputs_dir, **kwargs):
    return pipeline.run_pipeline(
        "p1",
        specs_dir=specs_dir,
        prompts_dir=PROMPTS_DIR,
        outputs_dir=outputs_dir,
        templates_dir=TEMPLATES_DIR,
        **kwargs,
    )


def test_first_run_calls_model_for_every_step_in_order(specs_dir, tmp_path):
    # Спек-фикстура заказывает стандартный чекер (checker.custom_needed:
    # false), поэтому checker_draft пропускается — он не менее часть
    # STEP_ORDER, но не вызывает модель.
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(specs_dir, outputs_dir)

    assert result.ok
    assert [o.step_name for o in result.outcomes] == pipeline.STEP_ORDER
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    called_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: statuses[s] for s in called_steps} == {s: "confirmed" for s in called_steps}
    assert mock_call.call_count == len(called_steps)
    # порядок вызовов совпадает с STEP_ORDER (за вычетом пропущенного checker_draft)
    assert [c.args[0] for c in mock_call.call_args_list] == called_steps


def test_second_run_is_all_cache_hits_and_does_not_call_model(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(specs_dir, outputs_dir)

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(specs_dir, outputs_dir)

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


def test_force_recalls_model_even_when_cached(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(specs_dir, outputs_dir)

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = _run(specs_dir, outputs_dir, force=True)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    # --force не отменяет "шаг вообще не применим для этой задачи" —
    # checker_draft всё равно skipped_optional при custom_needed=false.
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    forced_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: statuses[s] for s in forced_steps} == {s: "confirmed" for s in forced_steps}
    assert mock_call.call_count == len(forced_steps)


def test_uncertain_step_stops_pipeline_before_later_steps(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response) as mock_call:
        result = _run(specs_dir, outputs_dir)

    assert not result.ok
    assert result.stopped_uncertain is True
    assert mock_call.call_count == 1
    assert len(result.outcomes) == 1
    assert result.outcomes[0].step_name == "statement_draft"
    assert result.outcomes[0].status == pipeline.OUTCOME_UNCERTAIN
    assert result.outcomes[0].notes == uncertain_response.notes
    assert not outputs_dir.exists() or list(outputs_dir.rglob("*")) == []


def test_missing_spec_yields_spec_error_without_raising(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    result = pipeline.run_pipeline(
        "does-not-exist",
        specs_dir=specs_dir,
        prompts_dir=PROMPTS_DIR,
        outputs_dir=outputs_dir,
        templates_dir=TEMPLATES_DIR,
    )
    assert not result.ok
    assert result.spec_error is not None
    assert result.outcomes == []


def test_solutions_draft_skipped_when_spec_has_no_solutions_section(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    content = (specs_dir / "p1.yaml").read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    # вырезаем секцию solutions целиком (от "solutions:" до "checker:")
    start = next(i for i, line in enumerate(lines) if line.startswith("solutions:"))
    end = next(i for i, line in enumerate(lines) if line.startswith("checker:"))
    (specs_dir / "p1.yaml").write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(specs_dir, outputs_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["solutions_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    assert not (outputs_dir / "p1" / "solutions").exists()


def test_checker_draft_skipped_when_custom_needed_is_false(specs_dir, tmp_path):
    # Фикстура valid-spec.yaml заказывает стандартный чекер (ncmp) —
    # checker.custom_needed: false, черновик не заказан.
    outputs_dir = tmp_path / "outputs"

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(specs_dir, outputs_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == pipeline.OUTCOME_SKIPPED_OPTIONAL
    assert not (outputs_dir / "p1" / "checker.cpp").exists()


def test_checker_draft_runs_when_custom_needed_is_true(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    content = (specs_dir / "p1.yaml").read_text(encoding="utf-8")
    content = content.replace("custom_needed: false", "custom_needed: true").replace(
        "custom_comparison_notes: null",
        'custom_comparison_notes: "Сравнивать числа с точностью 1e-6"',
    )
    (specs_dir / "p1.yaml").write_text(content, encoding="utf-8")

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        result = _run(specs_dir, outputs_dir)

    assert result.ok
    statuses = {o.step_name: o.status for o in result.outcomes}
    assert statuses["checker_draft"] == "confirmed"
    assert (outputs_dir / "p1" / "checker.cpp").exists()


def test_only_step_without_prior_constraints_yaml_reports_error_not_crash(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step="generators_and_script",
            specs_dir=specs_dir,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    assert not result.ok
    assert result.stopped_uncertain is True
    assert result.outcomes[0].step_name == "generators_and_script"
    assert result.outcomes[0].status == "error"
    assert "constraints.yaml" in result.outcomes[0].detail
    mock_call.assert_not_called()


def test_only_step_picks_up_constraints_yaml_written_by_earlier_run(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        pipeline.run_pipeline(
            "p1",
            only_step="constraints_pick",
            specs_dir=specs_dir,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model) as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step="generators_and_script",
            specs_dir=specs_dir,
            prompts_dir=PROMPTS_DIR,
            outputs_dir=outputs_dir,
            templates_dir=TEMPLATES_DIR,
        )

    assert result.ok
    assert result.outcomes[0].status == "confirmed"
    assert mock_call.call_count == 1
    assert mock_call.call_args.args[0] == "generators_and_script"


# --- compute_step_statuses ----------------------------------------------------


def test_compute_step_statuses_matches_cache_hits_after_full_run(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(specs_dir, outputs_dir)

    statuses, spec_error = pipeline.compute_step_statuses(
        "p1", specs_dir=specs_dir, prompts_dir=PROMPTS_DIR, outputs_dir=outputs_dir
    )
    assert spec_error is None
    by_name = {s.step_name: s.state for s in statuses}
    assert by_name["checker_draft"] == pipeline.STATUS_SKIPPED_OPTIONAL
    cached_steps = [s for s in pipeline.STEP_ORDER if s != "checker_draft"]
    assert {s: by_name[s] for s in cached_steps} == {
        s: pipeline.STATUS_CACHE_HIT for s in cached_steps
    }


def test_compute_step_statuses_not_run_when_no_cache(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    statuses, spec_error = pipeline.compute_step_statuses(
        "p1", specs_dir=specs_dir, prompts_dir=PROMPTS_DIR, outputs_dir=outputs_dir
    )
    assert spec_error is None
    assert statuses[0].state == pipeline.STATUS_NOT_RUN


def test_compute_step_statuses_stale_after_spec_edit(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    with patch("orchestrator.model_router.call_model", side_effect=_fake_call_model):
        _run(specs_dir, outputs_dir)

    content = (specs_dir / "p1.yaml").read_text(encoding="utf-8")
    content = content.replace("Есть граф городов и дорог", "Есть граф городов и дорог (отредактировано)")
    (specs_dir / "p1.yaml").write_text(content, encoding="utf-8")

    statuses, _ = pipeline.compute_step_statuses(
        "p1", specs_dir=specs_dir, prompts_dir=PROMPTS_DIR, outputs_dir=outputs_dir
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


def test_compute_step_statuses_not_run_after_uncertain_run(specs_dir, tmp_path):
    # run_generative_step не пишет CacheEntry при status: uncertain (см.
    # test_run_generative_step_uncertain_raises_and_writes_nothing в
    # tests/test_steps_base.py) — поэтому status для такого шага "not run",
    # а не "uncertain": кэша просто нет, и следующий запуск снова позовёт
    # модель, что и требуется (CLAUDE.md, "провал по недостатку данных
    # должен всплывать при каждом запуске").
    outputs_dir = tmp_path / "outputs"
    uncertain_response = ModelResponse(
        status="uncertain",
        artifacts={},
        notes=[{"field": "statement_draft.formal_output_sketch", "kind": "uncertain", "explanation": "нет точности"}],
    )
    with patch("orchestrator.model_router.call_model", return_value=uncertain_response):
        _run(specs_dir, outputs_dir)

    statuses, _ = pipeline.compute_step_statuses(
        "p1", specs_dir=specs_dir, prompts_dir=PROMPTS_DIR, outputs_dir=outputs_dir
    )
    assert statuses[0].state == pipeline.STATUS_NOT_RUN


def test_compute_step_statuses_reports_spec_error(specs_dir, tmp_path):
    outputs_dir = tmp_path / "outputs"
    statuses, spec_error = pipeline.compute_step_statuses(
        "does-not-exist", specs_dir=specs_dir, prompts_dir=PROMPTS_DIR, outputs_dir=outputs_dir
    )
    assert statuses == []
    assert spec_error is not None
