from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import pipeline
from orchestrator.cache import load_cache_entry
from orchestrator.limits import LimitsError, parse_limits, render_limits

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
TEMPLATES_DIR = REPO_ROOT / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

STEP = "limits_from_constraints"


@pytest.fixture
def problems_dir(tmp_path):
    path = tmp_path / "problems" / "p1" / "spec.yaml"
    path.parent.mkdir(parents=True)
    content = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    path.write_text(content.replace("problem_id: valid-spec", "problem_id: p1"), encoding="utf-8")
    return tmp_path / "problems"


def _write_constraints(problems_dir: Path, time_limit_seconds, memory_limit_mb=256) -> None:
    (problems_dir / "p1" / "constraints.yaml").write_text(
        "n_max: 100000\n"
        f"limits:\n  time_limit_seconds: {time_limit_seconds}\n  memory_limit_mb: {memory_limit_mb}\n",
        encoding="utf-8",
    )


def _limits_path(problems_dir: Path) -> Path:
    return problems_dir / "p1" / "limits.yaml"


def _run(problems_dir: Path, **kwargs) -> pipeline.StepOutcome:
    with patch("orchestrator.model_router.call_model") as mock_call:
        result = pipeline.run_pipeline(
            "p1",
            only_step=STEP,
            prompts_dir=PROMPTS_DIR,
            problems_dir=problems_dir,
            templates_dir=TEMPLATES_DIR,
            **kwargs,
        )
    mock_call.assert_not_called()
    [outcome] = result.outcomes
    return outcome


def _status(problems_dir: Path) -> pipeline.StepStatus:
    statuses, error = pipeline.compute_step_statuses(
        "p1", prompts_dir=PROMPTS_DIR, problems_dir=problems_dir, templates_dir=TEMPLATES_DIR
    )
    assert error is None
    return next(status for status in statuses if status.step_name == STEP)


def test_derives_limits_from_constraints(problems_dir):
    _write_constraints(problems_dir, 1.5, 512)

    outcome = _run(problems_dir)

    assert outcome.status == "confirmed"
    assert parse_limits(_limits_path(problems_dir).read_text()) == (1500, 512)
    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT


def test_edited_limits_survive_when_constraints_limits_unchanged(problems_dir):
    _write_constraints(problems_dir, 1)
    _run(problems_dir)
    _limits_path(problems_dir).write_text("time_limit_seconds: 3\nmemory_limit_mb: 256\n")
    # Правка constraints.yaml не в TL/ML — вход шага тот же.
    (problems_dir / "p1" / "constraints.yaml").write_text(
        "n_max: 200000\nlimits:\n  time_limit_seconds: 1\n  memory_limit_mb: 256\n"
    )

    outcome = _run(problems_dir)

    assert outcome.status == pipeline.OUTCOME_CACHE_HIT
    assert parse_limits(_limits_path(problems_dir).read_text()) == (3000, 256)


def test_changed_constraints_limits_replace_limits_with_backup(problems_dir):
    _write_constraints(problems_dir, 1)
    _run(problems_dir)
    edited = "time_limit_seconds: 3\nmemory_limit_mb: 256\n"
    _limits_path(problems_dir).write_text(edited)
    _write_constraints(problems_dir, 2)
    assert _status(problems_dir).state == pipeline.STATUS_STALE

    outcome = _run(problems_dir)

    assert outcome.status == "confirmed"
    assert parse_limits(_limits_path(problems_dir).read_text()) == (2000, 256)
    assert (outcome.backup_dir / "limits.yaml").read_text() == edited
    assert [note["field"] for note in outcome.notes] == ["limits.yaml"]


def test_existing_limits_are_adopted_when_step_never_ran(problems_dir):
    _write_constraints(problems_dir, 1)
    pulled = render_limits(4000, 256)
    _limits_path(problems_dir).write_text(pulled)
    assert _status(problems_dir).state == pipeline.STATUS_NOT_RUN

    outcome = _run(problems_dir)

    assert outcome.status == pipeline.OUTCOME_CACHE_HIT
    assert "kept as is" in outcome.detail
    assert _limits_path(problems_dir).read_text() == pulled
    assert load_cache_entry("p1", STEP, problems_dir=problems_dir).accepted
    assert _status(problems_dir).state == pipeline.STATUS_CACHE_HIT


@pytest.mark.parametrize("flag", ["force", "fresh"])
def test_force_derives_again(problems_dir, flag):
    _write_constraints(problems_dir, 1)
    _limits_path(problems_dir).write_text(render_limits(4000, 256))

    outcome = _run(problems_dir, **{flag: True})

    assert outcome.status == "confirmed"
    assert parse_limits(_limits_path(problems_dir).read_text()) == (1000, 256)


def test_constraints_without_limits_stop_the_pipeline(problems_dir):
    (problems_dir / "p1" / "constraints.yaml").write_text("n_max: 100000\n")

    outcome = _run(problems_dir)

    assert outcome.status == pipeline.OUTCOME_ERROR
    assert "TL/ML in constraints.yaml" in outcome.detail
    assert not _limits_path(problems_dir).exists()


# --- orchestrator/limits.py ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("time_limit_seconds: 2\nmemory_limit_mb: 256\n", (2000, 256)),
        ("limits:\n  time_limit_seconds: 2.01\n  memory_limit_mb: 64\n", (2010, 64)),
        (render_limits(1500, 512), (1500, 512)),
    ],
)
def test_parse_limits(text, expected):
    assert parse_limits(text) == expected


@pytest.mark.parametrize(
    "text, message",
    [
        ("- a\n", "mapping"),
        ("groups: []\n", "found neither"),
        ("time_limit_seconds: null\nmemory_limit_mb: 256\n", "not set"),
        ("time_limit_seconds: 0\nmemory_limit_mb: 256\n", "positive number"),
        (
            "time_limit_seconds: 1\nmemory_limit_mb: 256\n"
            "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n",
            "different values",
        ),
    ],
)
def test_parse_limits_errors(text, message):
    with pytest.raises(LimitsError, match=message):
        parse_limits(text)
