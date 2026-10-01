from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from orchestrator import cli
from orchestrator.polygon import pipeline as polygon_pipeline
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.pipeline import (
    POLYGON_STEP_ORDER,
    compute_polygon_step_statuses,
    run_polygon_pipeline,
)
from orchestrator.polygon.state import PolygonState, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStep

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"


def _write_spec(
    specs_dir: Path, problem_id: str, *, file_stem: str | None = None, flat_script: bool = False
) -> None:
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    content = content.replace("problem_id: valid-spec", f"problem_id: {problem_id}")
    if flat_script:
        # upload_test_script отклоняет groups — для полного прогона нужен flat
        content = content.replace('script_style: "groups"', 'script_style: "flat"')
    (specs_dir / f"{file_stem or problem_id}.yaml").write_text(content, encoding="utf-8")


@pytest.fixture
def specs_dir(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    return specs


@pytest.fixture
def outputs_dir(tmp_path):
    return tmp_path / "outputs"


def _fake_step(
    name: str, calls: list[str], *, error: bool = False, commits_changes: bool = False
) -> PolygonStep:
    def execute(ctx, client):
        calls.append(name)
        if error:
            raise PolygonApiError(f"{name}.method", "boom")
        return f"{name} done"

    return PolygonStep(
        name=name,
        check_done=lambda ctx: None,
        execute=execute,
        compute_status=lambda problem_id, outputs_dir: ("not run", ""),
        commits_changes=commits_changes,
    )


@pytest.fixture
def fake_steps(monkeypatch):
    """Подменяет реестр polygon-шагов на три фейковых, записывающих порядок вызовов."""
    calls: list[str] = []

    def install(*, failing: str | None = None, commits_changes: bool = False) -> list[str]:
        names = ["step_a", "step_b", "step_c"]
        monkeypatch.setattr(polygon_pipeline, "POLYGON_STEP_ORDER", names)
        monkeypatch.setattr(
            polygon_pipeline,
            "_POLYGON_STEPS",
            {
                n: _fake_step(n, calls, error=(n == failing), commits_changes=commits_changes)
                for n in names
            },
        )
        return calls

    return install


def test_step_order_starts_with_create_problem():
    assert POLYGON_STEP_ORDER[0] == "create_problem"


def test_runs_all_steps_in_order(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    calls = fake_steps()

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=MagicMock()
    )

    assert calls == ["step_a", "step_b", "step_c"]
    assert [(o.step_name, o.status) for o in result.outcomes] == [
        ("step_a", "ok"),
        ("step_b", "ok"),
        ("step_c", "ok"),
    ]
    assert result.outcomes[0].detail == "step_a done"
    assert result.ok


def test_only_step_runs_just_that_step(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    calls = fake_steps()

    result = run_polygon_pipeline(
        PROBLEM_ID,
        only_step="step_b",
        specs_dir=specs_dir,
        outputs_dir=outputs_dir,
        client=MagicMock(),
    )

    assert calls == ["step_b"]
    assert [o.step_name for o in result.outcomes] == ["step_b"]
    assert result.ok


def test_polygon_api_error_stops_pipeline(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    calls = fake_steps(failing="step_b")

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=MagicMock()
    )

    assert calls == ["step_a", "step_b"]
    assert [(o.step_name, o.status) for o in result.outcomes] == [
        ("step_a", "ok"),
        ("step_b", "error"),
    ]
    assert "boom" in result.outcomes[-1].detail
    assert result.ok is False


def test_on_outcome_called_after_each_step_including_failed(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    calls = fake_steps(failing="step_b")
    seen = []

    def on_outcome(outcome):
        # к моменту вызова следующий шаг ещё не запускался
        seen.append((outcome.step_name, outcome.status, list(calls)))

    run_polygon_pipeline(
        PROBLEM_ID,
        specs_dir=specs_dir,
        outputs_dir=outputs_dir,
        client=MagicMock(),
        on_outcome=on_outcome,
    )

    assert seen == [
        ("step_a", "ok", ["step_a"]),
        ("step_b", "error", ["step_a", "step_b"]),
    ]


def test_unexpected_exception_is_step_error_with_traceback_in_log(
    specs_dir, outputs_dir, fake_steps, monkeypatch, caplog
):
    _write_spec(specs_dir, PROBLEM_ID)
    calls = fake_steps()

    def explode(ctx, client):
        calls.append("step_b")
        raise ConnectionError("network down")

    steps = dict(polygon_pipeline._POLYGON_STEPS)
    steps["step_b"] = PolygonStep(
        name="step_b",
        check_done=lambda ctx: None,
        execute=explode,
        compute_status=lambda problem_id, outputs_dir: ("not run", ""),
        commits_changes=False,
    )
    monkeypatch.setattr(polygon_pipeline, "_POLYGON_STEPS", steps)

    with caplog.at_level("ERROR", logger="orchestrator.polygon.pipeline"):
        result = run_polygon_pipeline(
            PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=MagicMock()
        )

    assert calls == ["step_a", "step_b"]
    assert [(o.step_name, o.status) for o in result.outcomes] == [
        ("step_a", "ok"),
        ("step_b", "error"),
    ]
    assert result.outcomes[-1].detail == "ConnectionError: network down"
    assert any(r.exc_info is not None for r in caplog.records)


def test_real_create_problem_step_with_mocked_client(specs_dir, outputs_dir):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.side_effect = lambda method, params: {
        "problems.list": [],
        "problem.create": {"id": 555},
    }[method]

    result = run_polygon_pipeline(
        PROBLEM_ID,
        only_step="create_problem",
        specs_dir=specs_dir,
        outputs_dir=outputs_dir,
        client=client,
    )

    assert result.ok
    assert [(o.step_name, o.status) for o in result.outcomes] == [("create_problem", "ok")]
    assert "555" in result.outcomes[0].detail


def test_real_create_problem_api_error_is_reported_not_raised(specs_dir, outputs_dir):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problems.list", "boom")

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert result.ok is False
    assert [(o.step_name, o.status) for o in result.outcomes] == [("create_problem", "error")]


def _link(outputs_dir: Path, polygon_id: int = 777) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID, polygon_id=polygon_id, created_at="2026-09-19T12:00:00+00:00"
        ),
        outputs_dir=outputs_dir,
    )


def _commit_call(step_name: str, polygon_id: int = 777):
    return call(
        "problem.commitChanges",
        {
            "problemId": str(polygon_id),
            "minorChanges": "true",
            "message": f"orchestrator: {step_name}",
        },
    )


def test_commits_after_each_successful_step(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    _link(outputs_dir)
    fake_steps(commits_changes=True)
    client = MagicMock()

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert result.ok
    assert client.call.call_args_list == [
        _commit_call("step_a"),
        _commit_call("step_b"),
        _commit_call("step_c"),
    ]
    assert result.outcomes[0].detail == "step_a done; committed on Polygon id=777"


def test_failed_step_is_not_committed_but_previous_are(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    _link(outputs_dir)
    fake_steps(failing="step_b", commits_changes=True)
    client = MagicMock()

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert not result.ok
    assert client.call.call_args_list == [_commit_call("step_a")]


def test_commit_error_marks_step_as_error_and_stops(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    _link(outputs_dir)
    calls = fake_steps(commits_changes=True)
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problem.commitChanges", "commit boom")

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert calls == ["step_a"]
    assert [(o.step_name, o.status) for o in result.outcomes] == [("step_a", "error")]
    assert "commit boom" in result.outcomes[0].detail


def test_commit_without_polygon_state_is_step_error(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    fake_steps(commits_changes=True)
    client = MagicMock()

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert [(o.step_name, o.status) for o in result.outcomes] == [("step_a", "error")]
    client.call.assert_not_called()


def test_create_problem_does_not_commit(specs_dir, outputs_dir):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.side_effect = lambda method, params: {
        "problems.list": [],
        "problem.create": {"id": 555},
    }[method]

    result = run_polygon_pipeline(
        PROBLEM_ID,
        only_step="create_problem",
        specs_dir=specs_dir,
        outputs_dir=outputs_dir,
        client=client,
    )

    assert result.ok
    assert [c.args[0] for c in client.call.call_args_list] == ["problems.list", "problem.create"]


def test_full_run_commits_after_every_step_but_create_problem(specs_dir, outputs_dir):
    _write_spec(specs_dir, PROBLEM_ID, flat_script=True)
    _write_generated_outputs(outputs_dir)
    client = MagicMock()
    client.call.side_effect = lambda method, params, **kwargs: (
        [{"id": 123, "name": PROBLEM_ID}] if method == "problems.list" else None
    )

    assert run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    ).ok

    commits = [
        c.args[1]["message"]
        for c in client.call.call_args_list
        if c.args[0] == "problem.commitChanges"
    ]
    assert commits == [f"orchestrator: {name}" for name in POLYGON_STEP_ORDER[1:]]


def test_step_order_has_set_constraints_after_create_problem():
    assert POLYGON_STEP_ORDER[:2] == ["create_problem", "set_constraints"]


def test_step_order_has_upload_problem_lib_after_set_constraints():
    assert POLYGON_STEP_ORDER[:3] == ["create_problem", "set_constraints", "upload_problem_lib"]


def test_step_order_has_upload_validator_after_upload_problem_lib():
    assert POLYGON_STEP_ORDER[:4] == [
        "create_problem",
        "set_constraints",
        "upload_problem_lib",
        "upload_validator",
    ]


def test_step_order_has_upload_checker_after_upload_validator():
    assert POLYGON_STEP_ORDER[:5] == [
        "create_problem",
        "set_constraints",
        "upload_problem_lib",
        "upload_validator",
        "upload_checker",
    ]


def test_step_order_has_upload_statement_after_upload_checker():
    assert POLYGON_STEP_ORDER[:6] == [
        "create_problem",
        "set_constraints",
        "upload_problem_lib",
        "upload_validator",
        "upload_checker",
        "upload_statement",
    ]


def test_step_order_has_upload_test_script_after_upload_generators():
    assert POLYGON_STEP_ORDER[:8] == [
        "create_problem",
        "set_constraints",
        "upload_problem_lib",
        "upload_validator",
        "upload_checker",
        "upload_statement",
        "upload_generators",
        "upload_test_script",
    ]


def test_step_order_ends_with_upload_solutions():
    assert POLYGON_STEP_ORDER == [
        "create_problem",
        "set_constraints",
        "upload_problem_lib",
        "upload_validator",
        "upload_checker",
        "upload_statement",
        "upload_generators",
        "upload_test_script",
        "upload_solutions",
    ]


def test_polygon_step_error_stops_pipeline_before_network(specs_dir, outputs_dir):
    # задача привязана, но constraints.yaml нет -> PolygonStepError в set_constraints
    _write_spec(specs_dir, PROBLEM_ID)
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(problem_id=PROBLEM_ID, polygon_id=777, created_at="2026-09-19T12:00:00+00:00"),
        outputs_dir=outputs_dir,
    )
    client = MagicMock()

    result = run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    )

    assert result.ok is False
    assert [(o.step_name, o.status) for o in result.outcomes] == [
        ("create_problem", "ok"),
        ("set_constraints", "error"),
    ]
    assert "constraints_pick" in result.outcomes[-1].detail
    client.call.assert_not_called()


def test_invalid_spec_sets_spec_error_and_never_creates_client(specs_dir, outputs_dir):
    # problem_id внутри файла не совпадает с именем файла -> SpecValidationError
    _write_spec(specs_dir, "other-problem", file_stem=PROBLEM_ID)

    with patch.object(polygon_pipeline, "PolygonClient") as client_cls:
        result = run_polygon_pipeline(PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir)

    assert result.spec_error
    assert result.outcomes == []
    assert result.ok is False
    client_cls.assert_not_called()


def test_client_created_once_when_not_passed(specs_dir, outputs_dir, fake_steps):
    _write_spec(specs_dir, PROBLEM_ID)
    fake_steps()

    with patch.object(polygon_pipeline, "PolygonClient") as client_cls:
        result = run_polygon_pipeline(PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir)

    assert result.ok
    client_cls.assert_called_once_with()


def test_status_not_run_without_state(tmp_path):
    statuses = compute_polygon_step_statuses(PROBLEM_ID, outputs_dir=tmp_path)

    assert [(s.step_name, s.status) for s in statuses] == [
        ("create_problem", "not run"),
        ("set_constraints", "not run"),
        ("upload_problem_lib", "not run"),
        ("upload_validator", "not run"),
        ("upload_checker", "not run"),
        ("upload_statement", "not run"),
        ("upload_generators", "not run"),
        ("upload_test_script", "not run"),
        ("upload_solutions", "not run"),
    ]


def test_status_done_with_state(tmp_path):
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(problem_id=PROBLEM_ID, polygon_id=777, created_at="2026-09-19T12:00:00+00:00"),
        outputs_dir=tmp_path,
    )

    statuses = compute_polygon_step_statuses(PROBLEM_ID, outputs_dir=tmp_path)

    assert [(s.step_name, s.status) for s in statuses] == [
        ("create_problem", "done"),
        ("set_constraints", "not run"),
        ("upload_problem_lib", "not run"),
        ("upload_validator", "not run"),
        ("upload_checker", "not run"),
        ("upload_statement", "not run"),
        ("upload_generators", "not run"),
        ("upload_test_script", "not run"),
        ("upload_solutions", "not run"),
    ]
    assert statuses[0].detail == "polygon_id=777"


def test_status_after_full_run_then_constraints_change(specs_dir, outputs_dir):
    _write_spec(specs_dir, PROBLEM_ID, flat_script=True)
    _write_generated_outputs(outputs_dir)
    client = MagicMock()
    client.call.side_effect = lambda method, params, **kwargs: {
        "problems.list": [{"id": 123, "name": PROBLEM_ID}],
        "problem.updateInfo": None,
        "problem.saveFile": None,
        "problem.setValidator": None,
        "problem.setChecker": None,
        "problem.saveStatement": None,
        "problem.saveTest": None,
        "problem.saveScript": None,
        "problem.saveSolution": None,
        "problem.commitChanges": None,
    }[method]
    assert run_polygon_pipeline(
        PROBLEM_ID, specs_dir=specs_dir, outputs_dir=outputs_dir, client=client
    ).ok

    statuses = compute_polygon_step_statuses(PROBLEM_ID, outputs_dir=outputs_dir)
    assert [(s.step_name, s.status) for s in statuses] == [
        ("create_problem", "done"),
        ("set_constraints", "done"),
        ("upload_problem_lib", "done"),
        ("upload_validator", "done"),
        ("upload_checker", "done"),
        ("upload_statement", "done"),
        ("upload_generators", "done"),
        ("upload_test_script", "done"),
        ("upload_solutions", "done"),
    ]

    (outputs_dir / PROBLEM_ID / "constraints.yaml").write_text(
        "limits:\n  time_limit_seconds: 3\n  memory_limit_mb: 256\n", encoding="utf-8"
    )
    statuses = compute_polygon_step_statuses(PROBLEM_ID, outputs_dir=outputs_dir)
    assert statuses[1].status == "stale"
    assert "2000" in statuses[1].detail and "3000" in statuses[1].detail


# --- CLI ---


def _cli_args(specs_dir: Path, outputs_dir: Path) -> list[str]:
    return ["--specs-dir", str(specs_dir), "--outputs-dir", str(outputs_dir)]


def _write_generated_outputs(outputs_dir: Path) -> None:
    path = outputs_dir / PROBLEM_ID / "constraints.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n", encoding="utf-8")
    (path.parent / "validator.cpp").write_text("int main() {}\n", encoding="utf-8")
    statement_dir = path.parent / "statement"
    (statement_dir / "examples").mkdir(parents=True, exist_ok=True)
    for name in ("legend.tex", "input_format.tex", "output_format.tex", "notes.tex"):
        (statement_dir / name).write_text("text\n", encoding="utf-8")
    (statement_dir / "examples" / "example_1.txt").write_text("1\n", encoding="utf-8")
    generators_dir = path.parent / "generators"
    generators_dir.mkdir(parents=True, exist_ok=True)
    (generators_dir / "gen_random.cpp").write_text("int main() {}\n", encoding="utf-8")
    (path.parent / "test_script").write_text("gen_random 1 > $\n", encoding="utf-8")
    solutions_dir = path.parent / "solutions"
    solutions_dir.mkdir(parents=True, exist_ok=True)
    (solutions_dir / "ok_cpp_main_draft.cpp").write_text("int main() {}\n", encoding="utf-8")


def test_cli_polygon_run_prints_outcomes(specs_dir, outputs_dir, capsys):
    _write_spec(specs_dir, PROBLEM_ID, flat_script=True)
    _write_generated_outputs(outputs_dir)
    client = MagicMock()
    client.call.side_effect = lambda method, params, **kwargs: {
        "problems.list": [{"id": 123, "name": PROBLEM_ID}],
        "problem.updateInfo": None,
        "problem.saveFile": None,
        "problem.setValidator": None,
        "problem.setChecker": None,
        "problem.saveStatement": None,
        "problem.saveTest": None,
        "problem.saveScript": None,
        "problem.saveSolution": None,
        "problem.commitChanges": None,
    }[method]

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "create_problem: ok" in out
    assert "set_constraints: ok" in out
    assert "upload_problem_lib: ok" in out
    assert "upload_validator: ok" in out
    assert "upload_checker: ok" in out
    assert "upload_statement: ok" in out
    assert "upload_generators: ok" in out
    assert "upload_test_script: ok" in out
    assert "upload_solutions: ok" in out


def test_cli_polygon_run_with_step(specs_dir, outputs_dir, capsys):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.return_value = [{"id": 123, "name": PROBLEM_ID}]

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        rc = cli.main(
            _cli_args(specs_dir, outputs_dir)
            + ["polygon", PROBLEM_ID, "run", "--step", "create_problem"]
        )

    assert rc == 0
    assert "create_problem: ok" in capsys.readouterr().out


def test_cli_polygon_run_api_error_exits_nonzero(specs_dir, outputs_dir, capsys):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problems.list", "boom")

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run"])

    assert rc == 1
    assert "create_problem: error" in capsys.readouterr().out


def test_cli_polygon_run_invalid_spec_exits_nonzero(specs_dir, outputs_dir, capsys):
    _write_spec(specs_dir, "other-problem", file_stem=PROBLEM_ID)

    rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run"])

    assert rc == 1
    assert capsys.readouterr().err


def test_cli_polygon_run_rejects_unknown_step(specs_dir, outputs_dir):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            _cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run", "--step", "nope"]
        )
    assert exc_info.value.code == 2


def test_cli_polygon_run_has_no_force_flag(specs_dir, outputs_dir):
    with pytest.raises(SystemExit):
        cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run", "--force"])


def test_cli_polygon_requires_subcommand(specs_dir, outputs_dir):
    with pytest.raises(SystemExit):
        cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID])


def test_cli_polygon_status_without_network(specs_dir, outputs_dir, capsys):
    with patch.object(polygon_pipeline, "PolygonClient") as client_cls:
        rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "status"])

    assert rc == 0
    assert "create_problem: not run" in capsys.readouterr().out
    client_cls.assert_not_called()


def test_cli_polygon_run_error_is_short_in_console_and_full_in_log(
    specs_dir, outputs_dir, capsys
):
    _write_spec(specs_dir, PROBLEM_ID)
    long_comment = "compilation failed\n" + "\n".join(f"error line {i}" for i in range(50))
    client = MagicMock()
    client.call.side_effect = PolygonApiError("problems.list", long_comment)

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "create_problem: error — Polygon API problems.list: compilation failed" in out
    assert "error line 1" not in out
    log_path = outputs_dir / PROBLEM_ID / cli.POLYGON_LOG
    assert str(log_path) in out
    log = log_path.read_text(encoding="utf-8")
    assert "error line 49" in log


def test_cli_polygon_run_unexpected_exception_has_no_traceback_in_console(
    specs_dir, outputs_dir, capsys
):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.side_effect = RuntimeError("POLYGON_API_KEY не задан")

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        rc = cli.main(_cli_args(specs_dir, outputs_dir) + ["polygon", PROBLEM_ID, "run"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "create_problem: error — RuntimeError: POLYGON_API_KEY не задан" in captured.out
    assert "Traceback" not in captured.out + captured.err
    log = (outputs_dir / PROBLEM_ID / cli.POLYGON_LOG).read_text(encoding="utf-8")
    assert "Traceback" in log


def test_cli_polygon_run_writes_step_progress_to_log(specs_dir, outputs_dir, capsys):
    _write_spec(specs_dir, PROBLEM_ID)
    client = MagicMock()
    client.call.return_value = [{"id": 123, "name": PROBLEM_ID}]

    with patch.object(polygon_pipeline, "PolygonClient", return_value=client):
        cli.main(
            _cli_args(specs_dir, outputs_dir)
            + ["polygon", PROBLEM_ID, "run", "--step", "create_problem"]
        )

    log = (outputs_dir / PROBLEM_ID / cli.POLYGON_LOG).read_text(encoding="utf-8")
    assert "[create_problem] start" in log
    assert not (outputs_dir / PROBLEM_ID / cli.LLM_GENERATION_LOG).exists()
