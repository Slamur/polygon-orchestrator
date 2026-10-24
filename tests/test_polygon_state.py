from dataclasses import replace
import json
from pathlib import Path

import pytest

from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    record_stamps,
    save_polygon_state,
    update_sync,
)


def _state() -> PolygonState:
    return PolygonState(
        problem_id="example-problem",
        polygon_id=123456,
        created_at="2026-09-19T12:00:00+00:00",
    )


def test_round_trip(tmp_path: Path):
    save_polygon_state("p1", _state(), problems_dir=tmp_path)

    assert load_polygon_state("p1", problems_dir=tmp_path) == _state()


def test_state_file_lives_next_to_cache_dir_not_inside_it(tmp_path: Path):
    save_polygon_state("p1", _state(), problems_dir=tmp_path)

    assert (tmp_path / "p1" / "polygon_state.json").is_file()
    assert not (tmp_path / "p1" / ".cache").exists()


def test_save_uses_cache_serialization_style(tmp_path: Path):
    save_polygon_state("p1", _state(), problems_dir=tmp_path)

    text = (tmp_path / "p1" / "polygon_state.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text == json.dumps(
        {
            "created_at": "2026-09-19T12:00:00+00:00",
            "polygon_id": 123456,
            "problem_id": "example-problem",
            "revision": None,
            "stamps": {},
            "synced": {},
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def test_save_overwrites_existing_state(tmp_path: Path):
    save_polygon_state("p1", _state(), problems_dir=tmp_path)
    newer = PolygonState(problem_id="p1", polygon_id=7, created_at="2026-09-20T00:00:00+00:00")

    save_polygon_state("p1", newer, problems_dir=tmp_path)

    assert load_polygon_state("p1", problems_dir=tmp_path) == newer


def test_load_missing_file_returns_none(tmp_path: Path):
    assert load_polygon_state("nope", problems_dir=tmp_path) is None


def test_load_broken_json_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_polygon_state("p1", problems_dir=tmp_path) is None


def test_load_non_dict_json_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert load_polygon_state("p1", problems_dir=tmp_path) is None


def test_load_wrong_fields_returns_none(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"problem_id": "p1", "extra": 1}), encoding="utf-8")

    assert load_polygon_state("p1", problems_dir=tmp_path) is None


def test_state_file_without_sync_fields_loads_with_unknown_baseline(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"problem_id": "p1", "polygon_id": 7, "created_at": "2026-09-19T12:00:00+00:00"}),
        encoding="utf-8",
    )

    state = load_polygon_state("p1", problems_dir=tmp_path)

    assert state is not None
    assert state.revision is None
    assert state.synced == {}


def test_legacy_steps_records_are_dropped_on_load(tmp_path: Path):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "problem_id": "p1",
                "polygon_id": 7,
                "created_at": "x",
                "steps": {"upload_validator": {"sha256": "abc"}},
            }
        ),
        encoding="utf-8",
    )

    state = load_polygon_state("p1", problems_dir=tmp_path)

    assert state == PolygonState(problem_id="p1", polygon_id=7, created_at="x")


@pytest.mark.parametrize("field, value", [("synced", ["nope"]), ("revision", "12")])
def test_malformed_sync_fields_treated_as_missing_state(tmp_path: Path, field, value):
    path = tmp_path / "p1" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"problem_id": "p1", "polygon_id": 7, "created_at": "x", field: value}),
        encoding="utf-8",
    )

    assert load_polygon_state("p1", problems_dir=tmp_path) is None


def test_update_sync_sets_and_removes_keys_and_replaces_revision(tmp_path: Path):
    save_polygon_state("p1", _state(), problems_dir=tmp_path)
    update_sync("p1", synced={"a": "1", "b": "2"}, revision=5, problems_dir=tmp_path)
    update_sync("p1", synced={"a": "3", "b": None, "c": None}, revision=6, problems_dir=tmp_path)

    state = load_polygon_state("p1", problems_dir=tmp_path)

    assert state.polygon_id == 123456
    assert state.synced == {"a": "3"}
    assert state.revision == 6


def test_update_sync_without_state_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        update_sync("p1", synced={}, revision=None, problems_dir=tmp_path)


def test_interrupted_save_keeps_previous_state(tmp_path, monkeypatch):
    problems_dir = tmp_path / "problems"
    state = PolygonState(problem_id="my-task", polygon_id=1, created_at="2026-09-19T12:00:00+00:00")
    save_polygon_state("my-task", state, problems_dir=problems_dir)

    def fail(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("orchestrator.polygon.state.os.replace", fail)
    with pytest.raises(KeyboardInterrupt):
        save_polygon_state("my-task", replace(state, polygon_id=2), problems_dir=problems_dir)

    assert load_polygon_state("my-task", problems_dir=problems_dir).polygon_id == 1
    assert [p.name for p in (problems_dir / "my-task").iterdir()] == ["polygon_state.json"]


def test_state_without_stamps_field_is_loaded(tmp_path: Path):
    path = tmp_path / "example-problem" / "polygon_state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "problem_id": "example-problem",
                "polygon_id": 1,
                "created_at": "2026-09-19T12:00:00+00:00",
                "revision": 3,
                "synced": {"spec.yaml": "h"},
            }
        ),
        encoding="utf-8",
    )

    state = load_polygon_state("example-problem", problems_dir=tmp_path)

    assert state.synced == {"spec.yaml": "h"}
    assert state.stamps == {}


def test_update_sync_writes_stamps_only_together_with_hashes(tmp_path: Path):
    save_polygon_state(
        "example-problem",
        replace(_state(), synced={"a": "1", "b": "2", "c": "3"}, stamps={"a": "s1", "b": "s2"}),
        problems_dir=tmp_path,
    )

    update_sync(
        "example-problem",
        # a — загружен без отметки (push), b — удалён, c — получает отметку,
        # d — без хеша: его отметка не пишется
        synced={"a": "1new", "b": None, "c": "3"},
        stamps={"c": "s3", "d": "s4"},
        revision=4,
        problems_dir=tmp_path,
    )

    state = load_polygon_state("example-problem", problems_dir=tmp_path)
    assert state.synced == {"a": "1new", "c": "3"}
    assert state.stamps == {"c": "s3"}


def test_update_sync_replace_drops_other_keys(tmp_path: Path):
    save_polygon_state(
        "example-problem",
        replace(_state(), synced={"old": "1"}, stamps={"old": "s"}),
        problems_dir=tmp_path,
    )

    update_sync(
        "example-problem", synced={"new": "2"}, revision=5, replace=True, problems_dir=tmp_path
    )

    state = load_polygon_state("example-problem", problems_dir=tmp_path)
    assert (state.synced, state.stamps, state.revision) == ({"new": "2"}, {}, 5)


def test_record_stamps_ignores_keys_without_hash(tmp_path: Path):
    save_polygon_state("example-problem", replace(_state(), synced={"a": "1"}), problems_dir=tmp_path)

    record_stamps("example-problem", {"a": "s1", "b": "s2"}, problems_dir=tmp_path)

    state = load_polygon_state("example-problem", problems_dir=tmp_path)
    assert state.stamps == {"a": "s1"}
    assert state.synced == {"a": "1"}
