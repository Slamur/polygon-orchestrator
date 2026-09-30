import json
from pathlib import Path

import pytest

from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    record_polygon_step,
    save_polygon_state,
)


def _state() -> PolygonState:
    return PolygonState(
        problem_id="example-problem",
        polygon_id=123456,
        created_at="2026-09-19T12:00:00+00:00",
    )


def test_round_trip(tmp_path: Path):
    save_polygon_state("p1", _state(), outputs_dir=tmp_path)

    assert load_polygon_state("p1", outputs_dir=tmp_path) == _state()


def test_state_file_lives_next_to_cache_dir_not_inside_it(tmp_path: Path):
    save_polygon_state("p1", _state(), outputs_dir=tmp_path)

    assert (tmp_path / "p1" / "polygon_state.json").is_file()
    assert not (tmp_path / "p1" / ".cache").exists()


def test_save_uses_cache_serialization_style(tmp_path: Path):
    save_polygon_state("p1", _state(), outputs_dir=tmp_path)

    text = (tmp_path / "p1" / "polygon_state.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text == json.dumps(
        {
            "created_at": "2026-09-19T12:00:00+00:00",
            "polygon_id": 123456,
            "problem_id": "example-problem",
            "steps": {},
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def test_save_overwrites_existing_state(tmp_path: Path):
    save_polygon_state("p1", _state(), outputs_dir=tmp_path)
    newer = PolygonState(problem_id="p1", polygon_id=7, created_at="2026-09-20T00:00:00+00:00")

    save_polygon_state("p1", newer, outputs_dir=tmp_path)

    assert load_polygon_state("p1", outputs_dir=tmp_path) == newer


def test_load_missing_file_returns_none(tmp_path: Path):
    assert load_polygon_state("nope", outputs_dir=tmp_path) is None


def test_load_broken_json_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_polygon_state("p1", outputs_dir=tmp_path) is None


def test_load_non_dict_json_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert load_polygon_state("p1", outputs_dir=tmp_path) is None


def test_load_wrong_fields_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"problem_id": "p1", "extra": 1}), encoding="utf-8")

    assert load_polygon_state("p1", outputs_dir=tmp_path) is None


def test_old_state_file_without_steps_still_loads(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"problem_id": "p1", "polygon_id": 7, "created_at": "2026-09-19T12:00:00+00:00"}),
        encoding="utf-8",
    )

    state = load_polygon_state("p1", outputs_dir=tmp_path)

    assert state is not None
    assert state.steps == {}


def test_non_dict_steps_treated_as_missing_state(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"problem_id": "p1", "polygon_id": 7, "created_at": "x", "steps": ["nope"]}
        ),
        encoding="utf-8",
    )

    assert load_polygon_state("p1", outputs_dir=tmp_path) is None


def test_record_polygon_step_keeps_link_and_other_steps(tmp_path: Path):
    save_polygon_state("p1", _state(), outputs_dir=tmp_path)
    record_polygon_step("p1", "a", {"x": 1}, outputs_dir=tmp_path)
    record_polygon_step("p1", "b", {"y": 2}, outputs_dir=tmp_path)
    record_polygon_step("p1", "a", {"x": 3}, outputs_dir=tmp_path)

    state = load_polygon_state("p1", outputs_dir=tmp_path)

    assert state.polygon_id == 123456
    assert state.steps == {"a": {"x": 3}, "b": {"y": 2}}


def test_record_polygon_step_without_state_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        record_polygon_step("p1", "a", {}, outputs_dir=tmp_path)
