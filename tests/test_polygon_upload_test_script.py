from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from orchestrator.checks.test_script_check import SKIP_CHECK_ENV, ScriptCheckResult, ScriptLine
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps import upload_test_script
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError
from orchestrator.polygon.steps.upload_test_script import STEP, STEP_NAME
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"
POLYGON_ID = 4242
SCRIPT = "gen_random 1 10 > $\ngen_random 2 100 > $\n"


@pytest.fixture
def groups_spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    assert spec.generation.script_style == "groups"
    return spec


@pytest.fixture
def spec(groups_spec):
    return groups_spec.model_copy(
        update={"generation": groups_spec.generation.model_copy(update={"script_style": "flat"})}
    )


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"


def _link(problems_dir: Path) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=POLYGON_ID,
            created_at="2026-09-19T12:00:00+00:00",
        ),
        problems_dir=problems_dir,
    )


def _write_script(problems_dir: Path, content: str = SCRIPT, name: str = "test_script") -> Path:
    path = problems_dir / PROBLEM_ID / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _run(spec, problems_dir, client, *, incremental: bool = False) -> str:
    return run_and_record(
        STEP, PROBLEM_ID, spec, problems_dir, client, incremental=incremental
    ).message


def test_step_is_a_polygon_step_with_expected_name():
    assert isinstance(STEP, PolygonStep)
    assert STEP.name == STEP_NAME == "upload_test_script"


def test_groups_style_rejected_before_network(problems_dir, groups_spec):
    # всё остальное на месте — отказ только из-за groups
    _link(problems_dir)
    _write_script(problems_dir, name="test_script_groups")
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="groups"):
        _run(groups_spec, problems_dir, client)

    client.call.assert_not_called()
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced == {}


def test_missing_polygon_state_points_to_create_problem(problems_dir, spec):
    _write_script(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="create_problem"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_missing_script_points_to_generators_and_script(problems_dir, spec):
    _link(problems_dir)
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="generators_and_script"):
        _run(spec, problems_dir, client)

    client.call.assert_not_called()


def test_uploads_script_via_save_script(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    client = MagicMock()

    message = _run(spec, problems_dir, client)

    client.call.assert_called_once_with(
        "problem.saveScript",
        {"problemId": str(POLYGON_ID), "testset": "tests", "source": SCRIPT},
        timeout=upload_test_script._SAVE_SCRIPT_TIMEOUT_SECONDS,
    )
    assert "test_script" in message and str(POLYGON_ID) in message


class FakeTime:
    """`time` шага: `sleep` только сдвигает часы и запоминает паузу."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(upload_test_script.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(upload_test_script.time, "sleep", fake.sleep)
    return fake


@pytest.fixture
def expanded(monkeypatch):
    """Скрипт раскрывается локально (`conftest` выключает проверку)."""
    monkeypatch.delenv(SKIP_CHECK_ENV)
    lines = [ScriptLine(i, text) for i, text in enumerate(SCRIPT.splitlines(), start=1)]
    monkeypatch.setattr(
        upload_test_script, "check_script", lambda *a, **k: ScriptCheckResult([], "", [], lines)
    )


def _tests(*script_lines: str) -> list[dict]:
    manual = [{"index": 1, "manual": True, "useInStatements": True}]
    return manual + [
        {"index": i, "manual": False, "useInStatements": False, "scriptLine": line}
        for i, line in enumerate(script_lines, start=2)
    ]


GENERATED = _tests("gen_random 1 10", "gen_random 2 100")


def _polygon(*tests_responses, save_times_out: bool = False) -> MagicMock:
    """Клиент: `problem.saveScript` (или его таймаут), затем `problem.tests`
    отвечает по очереди `tests_responses` (исключение — бросает его)."""
    responses = iter(tests_responses)

    def call(method, params, **kwargs):
        if method == "problem.saveScript":
            if save_times_out:
                raise requests.ReadTimeout("read timed out")
            return None
        assert method == "problem.tests"
        assert params["noInputs"] == "true"
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    client = MagicMock()
    client.call.side_effect = call
    return client


def test_generated_tests_are_checked_after_save(problems_dir, spec, expanded, fake_time):
    _link(problems_dir)
    _write_script(problems_dir)
    client = _polygon(GENERATED)

    message = _run(spec, problems_dir, client)

    assert [c.args[0] for c in client.call.call_args_list] == [
        "problem.saveScript",
        "problem.tests",
    ]
    assert "waited" not in message
    assert fake_time.sleeps == []


def test_waits_with_backoff_until_tests_match_script(problems_dir, spec, expanded, fake_time):
    _link(problems_dir)
    _write_script(problems_dir)
    old = _tests("gen_random 1 5", "gen_random 2 50")
    client = _polygon(old, requests.ReadTimeout("busy"), GENERATED[:2], GENERATED, save_times_out=True)

    message = _run(spec, problems_dir, client)

    assert fake_time.sleeps == [5, 10, 20]
    assert "waited 35s for test generation" in message
    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_gives_up_when_tests_never_match(problems_dir, spec, expanded, fake_time):
    _link(problems_dir)
    _write_script(problems_dir)
    client = _polygon(*[_tests("gen_random 1 10")] * 100)

    with pytest.raises(PolygonStepError, match=r"1/2 tests match the script"):
        _run(spec, problems_dir, client)

    assert max(fake_time.sleeps) == upload_test_script._GENERATION_POLL_MAX_DELAY_SECONDS
    assert sum(fake_time.sleeps) >= upload_test_script._GENERATION_WAIT_SECONDS
    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")


def test_timeout_without_expanded_script_is_an_error(problems_dir, spec):
    # conftest выключает проверку — скрипт не раскрыт, ждать нечего
    _link(problems_dir)
    _write_script(problems_dir)
    client = _polygon(save_times_out=True)

    with pytest.raises(PolygonStepError, match="cannot be checked"):
        _run(spec, problems_dir, client)

    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")


def test_command_is_compared_without_redirect_and_extra_spaces():
    assert upload_test_script._normalize_command("gen  -n=1   -k=2 > $") == "gen -n=1 -k=2"


def test_status_not_run_before_upload(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)

    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")


def test_incremental_run_skips_unchanged_script(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())
    client = MagicMock()

    assert _run(spec, problems_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_status_done_then_stale_after_script_change(problems_dir, spec):
    _link(problems_dir)
    path = _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())

    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    path.write_text(SCRIPT + "gen_random 3 1000 > $\n", encoding="utf-8")
    status, detail = STEP.compute_status(PROBLEM_ID, problems_dir)
    assert status == "stale"
    assert "test_script" in detail


def test_blank_lines_do_not_change_script_hash(problems_dir, spec):
    # Polygon выкидывает пустые строки при сохранении скрипта.
    _link(problems_dir)
    path = _write_script(problems_dir, "gen_random 1 10 > $\n\n  \ngen_random 2 100 > $  \n\n")
    _run(spec, problems_dir, MagicMock())

    path.write_text(SCRIPT.replace("\n", "\r\n"), encoding="utf-8")
    client = MagicMock()

    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"
    assert _run(spec, problems_dir, client, incremental=True) == "no changes since the last sync, skipped"
    client.call.assert_not_called()


def test_blank_lines_are_kept_in_uploaded_source(problems_dir, spec):
    _link(problems_dir)
    content = "gen_random 1 10 > $\n\ngen_random 2 100 > $\n"
    _write_script(problems_dir, content)
    client = MagicMock()

    _run(spec, problems_dir, client)

    assert client.call.call_args.args[1]["source"] == content


def test_status_stale_when_script_removed(problems_dir, spec):
    _link(problems_dir)
    path = _write_script(problems_dir)
    _run(spec, problems_dir, MagicMock())

    path.unlink()

    assert STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "stale"


def test_api_error_does_not_record_step(problems_dir, spec):
    _link(problems_dir)
    _write_script(problems_dir)
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problem.saveScript", "boom")

    with pytest.raises(PolygonApiError):
        _run(spec, problems_dir, client)

    assert STEP.compute_status(PROBLEM_ID, problems_dir) == ("not run", "")
