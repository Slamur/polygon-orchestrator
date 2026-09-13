import json
from pathlib import Path

from orchestrator.cache import (
    CacheEntry,
    compute_input_hash,
    compute_prompt_hash,
    is_cache_valid,
    load_cache_entry,
    save_cache_entry,
)


def _write_prompt(
    prompts_dir: Path, step_name: str, system_text: str, user_text: str = "user template"
) -> None:
    step_dir = prompts_dir / step_name
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "system.md").write_text(system_text, encoding="utf-8")
    (step_dir / "user.md.j2").write_text(user_text, encoding="utf-8")


# --- compute_input_hash -----------------------------------------------------


def test_compute_input_hash_stable_for_same_input():
    spec_section = {"intended_complexity": "O(N log N)", "variables": [{"name": "n"}]}
    h1 = compute_input_hash("constraints_pick", spec_section)
    h2 = compute_input_hash("constraints_pick", dict(spec_section))
    assert h1 == h2


def test_compute_input_hash_ignores_key_order():
    h1 = compute_input_hash("constraints_pick", {"a": 1, "b": 2})
    h2 = compute_input_hash("constraints_pick", {"b": 2, "a": 1})
    assert h1 == h2


def test_compute_input_hash_changes_when_spec_section_changes():
    h1 = compute_input_hash("constraints_pick", {"intended_complexity": "O(N)"})
    h2 = compute_input_hash("constraints_pick", {"intended_complexity": "O(N log N)"})
    assert h1 != h2


def test_compute_input_hash_changes_when_upstream_artifacts_change():
    spec_section = {"input_shape": "array"}
    h1 = compute_input_hash("generators_and_script", spec_section, upstream_artifacts={"tl": 1})
    h2 = compute_input_hash("generators_and_script", spec_section, upstream_artifacts={"tl": 2})
    assert h1 != h2


# --- compute_prompt_hash -----------------------------------------------------


def test_compute_prompt_hash_stable_when_files_unchanged(tmp_path):
    _write_prompt(tmp_path, "constraints_pick", "rule v1", "user v1")
    h1 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)
    h2 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)
    assert h1 == h2


def test_compute_prompt_hash_changes_when_system_md_changes(tmp_path):
    _write_prompt(tmp_path, "constraints_pick", "rule v1")
    h1 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)

    _write_prompt(tmp_path, "constraints_pick", "rule v2")
    h2 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)

    assert h1 != h2


def test_compute_prompt_hash_changes_when_user_template_changes(tmp_path):
    _write_prompt(tmp_path, "constraints_pick", "rule v1", "user v1")
    h1 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)

    _write_prompt(tmp_path, "constraints_pick", "rule v1", "user v2")
    h2 = compute_prompt_hash("constraints_pick", prompts_dir=tmp_path)

    assert h1 != h2


# --- load/save CacheEntry -----------------------------------------------------


def test_save_and_load_cache_entry_roundtrip(tmp_path):
    entry = CacheEntry(
        input_hash="abc",
        prompt_hash="def",
        model="strong-model-v1",
        status="ok",
        timestamp="2026-09-13T00:00:00Z",
    )
    save_cache_entry("problem1", "constraints_pick", entry, outputs_dir=tmp_path)

    loaded = load_cache_entry("problem1", "constraints_pick", outputs_dir=tmp_path)
    assert loaded == entry

    cache_file = tmp_path / "problem1" / ".cache" / "constraints_pick.json"
    assert cache_file.exists()
    raw = json.loads(cache_file.read_text(encoding="utf-8"))
    assert raw == {
        "input_hash": "abc",
        "prompt_hash": "def",
        "model": "strong-model-v1",
        "status": "ok",
        "timestamp": "2026-09-13T00:00:00Z",
    }


def test_load_cache_entry_missing_file_returns_none(tmp_path):
    assert load_cache_entry("no-such-problem", "constraints_pick", outputs_dir=tmp_path) is None


def test_load_cache_entry_malformed_json_returns_none(tmp_path):
    path = tmp_path / "problem1" / ".cache" / "constraints_pick.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    assert load_cache_entry("problem1", "constraints_pick", outputs_dir=tmp_path) is None


# --- is_cache_valid -----------------------------------------------------


def test_is_cache_valid_true_when_hashes_match():
    entry = CacheEntry("h1", "p1", "model", "ok", "2026-01-01T00:00:00Z")
    assert is_cache_valid(entry, "h1", "p1") is True


def test_is_cache_valid_false_when_spec_input_hash_changed():
    entry = CacheEntry("h1", "p1", "model", "ok", "2026-01-01T00:00:00Z")
    assert is_cache_valid(entry, "h2", "p1") is False


def test_is_cache_valid_false_when_prompt_hash_changed():
    entry = CacheEntry("h1", "p1", "model", "ok", "2026-01-01T00:00:00Z")
    assert is_cache_valid(entry, "h1", "p2") is False


def test_is_cache_valid_false_when_status_uncertain_even_with_matching_hashes():
    entry = CacheEntry("h1", "p1", "model", "uncertain", "2026-01-01T00:00:00Z")
    assert is_cache_valid(entry, "h1", "p1") is False


def test_is_cache_valid_false_when_no_entry():
    assert is_cache_valid(None, "h1", "p1") is False
