import json
from pathlib import Path

import pytest

from orchestrator.cache import (
    CacheEntry,
    compute_input_hash,
    compute_input_parts,
    compute_prompt_hash,
    describe_changed_parts,
    is_cache_valid,
    load_cache_entry,
    save_cache_entry,
)
from orchestrator.steps.base import STEP_CONTEXT_DOCUMENTS


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
    h1 = compute_input_hash("tests_plan", spec_section, upstream_artifacts={"tl": 1})
    h2 = compute_input_hash("tests_plan", spec_section, upstream_artifacts={"tl": 2})
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


def _write_context_doc(templates_dir: Path, rel_path: str, content: str) -> None:
    path = templates_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_step_context_docs(templates_dir: Path, step_name: str) -> None:
    for rel_path in STEP_CONTEXT_DOCUMENTS[step_name]:
        _write_context_doc(templates_dir, rel_path, f"{rel_path} v1")


def test_compute_prompt_hash_changes_when_context_document_changes(tmp_path):
    prompts_dir = tmp_path / "prompts"
    templates_dir = tmp_path / "templates"
    _write_prompt(prompts_dir, "constraints_pick", "rule v1", "user v1")
    _write_step_context_docs(templates_dir, "constraints_pick")

    h1 = compute_prompt_hash("constraints_pick", prompts_dir=prompts_dir, templates_dir=templates_dir)

    _write_context_doc(templates_dir, "tutorials/limits.md", "limits v2")
    h2 = compute_prompt_hash("constraints_pick", prompts_dir=prompts_dir, templates_dir=templates_dir)

    assert h1 != h2


def test_compute_prompt_hash_unaffected_by_other_steps_context_document(tmp_path):
    prompts_dir = tmp_path / "prompts"
    templates_dir = tmp_path / "templates"
    _write_prompt(prompts_dir, "constraints_pick", "rule v1", "user v1")
    _write_step_context_docs(templates_dir, "constraints_pick")
    _write_context_doc(templates_dir, "tutorials/statement.md", "statement v1")

    h1 = compute_prompt_hash("constraints_pick", prompts_dir=prompts_dir, templates_dir=templates_dir)

    # constraints_pick не читает statement.md (это документ statement_draft) —
    # его правка не должна инвалидировать кэш constraints_pick.
    _write_context_doc(templates_dir, "tutorials/statement.md", "statement v2")
    h2 = compute_prompt_hash("constraints_pick", prompts_dir=prompts_dir, templates_dir=templates_dir)

    assert h1 == h2


def test_compute_prompt_hash_includes_extra_context_documents(tmp_path):
    prompts_dir = tmp_path / "prompts"
    templates_dir = tmp_path / "templates"
    _write_prompt(prompts_dir, "tests_plan", "rule v1", "user v1")
    _write_step_context_docs(templates_dir, "tests_plan")
    extra_file = tmp_path / "extra_gen.cpp"
    extra_file.write_text("extra v1", encoding="utf-8")

    h1 = compute_prompt_hash(
        "tests_plan",
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=[str(extra_file)],
    )

    extra_file.write_text("extra v2", encoding="utf-8")
    h2 = compute_prompt_hash(
        "tests_plan",
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=[str(extra_file)],
    )

    assert h1 != h2


def test_compute_prompt_hash_missing_context_document_raises(tmp_path):
    prompts_dir = tmp_path / "prompts"
    templates_dir = tmp_path / "templates"
    _write_prompt(prompts_dir, "constraints_pick", "rule v1", "user v1")

    with pytest.raises(FileNotFoundError, match="tutorials/limits.md"):
        compute_prompt_hash("constraints_pick", prompts_dir=prompts_dir, templates_dir=templates_dir)


# --- load/save CacheEntry -----------------------------------------------------


def test_save_and_load_cache_entry_roundtrip(tmp_path):
    entry = CacheEntry(
        input_hash="abc",
        prompt_hash="def",
        model="strong-model-v1",
        status="ok",
        timestamp="2026-09-13T00:00:00Z",
    )
    save_cache_entry("problem1", "constraints_pick", entry, problems_dir=tmp_path)

    loaded = load_cache_entry("problem1", "constraints_pick", problems_dir=tmp_path)
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
        "input_parts": None,
        "prompt_parts": None,
        "input_values": None,
        "accepted": False,
        "conflicts": None,
    }


def test_load_cache_entry_missing_file_returns_none(tmp_path):
    assert load_cache_entry("no-such-problem", "constraints_pick", problems_dir=tmp_path) is None


def test_load_cache_entry_malformed_json_returns_none(tmp_path):
    path = tmp_path / "problem1" / ".cache" / "constraints_pick.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    assert load_cache_entry("problem1", "constraints_pick", problems_dir=tmp_path) is None


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


# --- хеши по частям и модель --------------------------------------------


def test_is_cache_valid_false_when_model_changed():
    entry = CacheEntry("h1", "p1", "medium-model", "confirmed", "t")
    assert is_cache_valid(entry, "h1", "p1", "medium-model") is True
    assert is_cache_valid(entry, "h1", "p1", "strong-model") is False


def test_compute_input_parts_splits_sections_two_levels_deep():
    parts = compute_input_parts(
        {"constraints": {"n": 1, "m": 2}, "flag": True}, {"constraints.yaml": "x"}
    )
    assert set(parts) == {
        "spec: constraints.n",
        "spec: constraints.m",
        "spec: flag",
        "upstream: constraints.yaml",
    }
    changed = compute_input_parts(
        {"constraints": {"n": 1, "m": 3}, "flag": True}, {"constraints.yaml": "x"}
    )
    assert describe_changed_parts(parts, changed) == ["spec: constraints.m"]


def test_compute_input_parts_of_non_dict_section():
    assert set(compute_input_parts("notes")) == {"spec"}


def test_describe_changed_parts_marks_added_and_removed():
    old = {"a": "1", "b": "2"}
    new = {"a": "1", "c": "3"}
    assert describe_changed_parts(old, new) == ["c (added)", "b (removed)"]
    assert describe_changed_parts(None, new) == []


def test_load_cache_entry_without_parts_fields_is_still_valid(tmp_path):
    cache_dir = tmp_path / "p1" / ".cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "statement_draft.json").write_text(
        json.dumps(
            {
                "input_hash": "h",
                "prompt_hash": "p",
                "model": "medium-model",
                "status": "confirmed",
                "timestamp": "t",
            }
        ),
        encoding="utf-8",
    )
    entry = load_cache_entry("p1", "statement_draft", problems_dir=tmp_path)
    assert entry is not None
    assert entry.input_parts is None and entry.prompt_parts is None
    assert entry.accepted is False
