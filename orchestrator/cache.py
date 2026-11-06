"""The cache by spec hash for the generative steps (see CLAUDE.md, the
"Caching by spec hash" section).

A step must not call the model again if nothing that
affects it has changed since the last successful run: neither the corresponding
spec section (+ the upstream artifacts of previous steps), nor the contents of the
prompt templates, nor the model from `model_router.py`.

This module only computes hashes and reads/writes `problems/<id>/.cache/<step>.json`
(and the model's unapplied response `.cache/<step>.response.json`, see
`PendingResponse`) — the decision whether to call the model is made by `pipeline.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orchestrator.problem_paths import PROBLEMS_DIR
from orchestrator.step_order import prompt_dir_name

PROMPTS_DIR = Path("prompts")
TEMPLATES_DIR = Path("templates")

# The revision-mode user-prompt block shared by all steps
# (`orchestrator/steps/base.py`), relative to prompts_dir. Part of every step's
# prompt hash: editing the block must invalidate the cache, like editing the
# step's prompts themselves.
REVISE_PROMPT = "revise.md.j2"

# The status that is never considered a valid cache (see CLAUDE.md,
# "Caching by spec hash", item 3): a failure due to missing data must
# surface on every run until the author fixes the spec.
UNCERTAIN_STATUS = "uncertain"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_input_hash(
    step_name: str,
    spec_section: Any,
    upstream_artifacts: Any = None,
) -> str:
    """sha256 of the canonical JSON representation of the step's input.

    `spec_section` — the part of the spec the step depends on (see CLAUDE.md:
    `statement_draft` -> the `statement_draft` section, `constraints_pick` ->
    the `constraints` section, `tests_plan` -> the `generation` section,
    `solutions_draft` -> the `solutions` section).
    `upstream_artifacts` — the artifacts of previous steps the current one
    depends on (e.g. the contents of `constraints.yaml` for steps 3 and 4);
    `None` for steps without such a dependency.
    """
    payload = {
        "step": step_name,
        "spec_section": spec_section,
        "upstream_artifacts": upstream_artifacts,
    }
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_input_values(spec_section: Any, upstream_artifacts: Any = None) -> dict[str, Any]:
    """The step's input by parts — the same data as `compute_input_hash`:
    `spec: <key>[.<subkey>]` for the fields of the spec section (two levels deep)
    and `upstream: <name>` for upstream artifacts. The values are stored in the cache
    (`CacheEntry.input_values`), so that the next run in revision mode
    shows the model what exactly changed (`changed_input_values`).
    """
    values: dict[str, Any] = {}
    if isinstance(spec_section, dict):
        for key, value in spec_section.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    values[f"spec: {key}.{sub_key}"] = sub_value
            else:
                values[f"spec: {key}"] = value
    else:
        values["spec"] = spec_section
    for name, content in (upstream_artifacts or {}).items():
        values[f"upstream: {name}"] = content
    return values


def input_parts_from_values(values: dict[str, Any]) -> dict[str, str]:
    """The hashes of the input parts (`compute_input_values`). The "is the cache valid"
    decision is made only by the overall hash, the parts are needed so that
    `llm status`/`llm generate` can say what exactly changed
    (`describe_changed_parts`)."""
    return {name: _sha256(_canonical_json(value)) for name, value in values.items()}


def compute_input_parts(spec_section: Any, upstream_artifacts: Any = None) -> dict[str, str]:
    """The hashes of the individual parts of the step's input, see `compute_input_values`."""
    return input_parts_from_values(compute_input_values(spec_section, upstream_artifacts))


def changed_input_values(
    old_values: dict[str, Any] | None, new_values: dict[str, Any]
) -> list[tuple[str, Any, Any]] | None:
    """The changed input parts: `(name, before, after)`; for a part that appeared
    "before" is `None`, for one that vanished "after" is `None`. `old_values=None` (a cache
    entry without values) — `None`: what changed is unknown."""
    if old_values is None:
        return None
    changes = [
        (name, old_values.get(name), value)
        for name, value in new_values.items()
        if name not in old_values or _canonical_json(old_values[name]) != _canonical_json(value)
    ]
    changes += [(name, value, None) for name, value in old_values.items() if name not in new_values]
    return changes


def compute_prompt_hash(
    step_name: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
) -> str:
    """sha256 of `prompts/<step_name>/{system.md,user.md.j2}` + the context documents.

    Reads the files from disk, so editing a prompt invalidates the step's cache even
    if the spec itself didn't change. Besides the prompts themselves (and the shared
    revision-mode block `REVISE_PROMPT`, if there is one), the hash also includes
    the contents of all of this step's context documents
    (`orchestrator.steps.base.STEP_CONTEXT_DOCUMENTS[step_name]` +
    `extra_context_documents`, see `orchestrator.steps.base.
    load_context_documents`) — otherwise editing tutorials/*.md/problem_lib.h/
    generators/*.cpp etc. wouldn't invalidate the cache of the steps that depend on them.
    This is our own result cache (`problems/<id>/.cache/<step>.json`),
    unrelated to Anthropic prompt caching.
    """
    system_text, user_text, context_documents = _read_prompt_inputs(
        step_name,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )

    digest = hashlib.sha256()
    digest.update(system_text.encode("utf-8"))
    digest.update(user_text.encode("utf-8"))
    revise_text = _read_revise_prompt(prompts_dir)
    if revise_text is not None:
        digest.update(revise_text.encode("utf-8"))
    for doc_path, content in context_documents.items():
        digest.update(doc_path.encode("utf-8"))
        digest.update(content.encode("utf-8"))

    return digest.hexdigest()


def compute_prompt_parts(
    step_name: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
) -> dict[str, str]:
    """Per-file hashes of the same set as `compute_prompt_hash`: both
    prompt templates of the step and each context document separately. Like
    `compute_input_parts`, only for explaining what changed."""
    system_text, user_text, context_documents = _read_prompt_inputs(
        step_name,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )
    prompt_dir = prompt_dir_name(step_name)
    parts = {
        f"prompt: {prompt_dir}/system.md": _sha256(system_text),
        f"prompt: {prompt_dir}/user.md.j2": _sha256(user_text),
    }
    revise_text = _read_revise_prompt(prompts_dir)
    if revise_text is not None:
        parts[f"prompt: {REVISE_PROMPT}"] = _sha256(revise_text)
    for doc_path, content in context_documents.items():
        parts[f"document: {doc_path}"] = _sha256(content)
    return parts


def _read_revise_prompt(prompts_dir: Path) -> str | None:
    """`prompts/revise.md.j2`, if it exists. If not — `None`: it isn't part of the hash
    (it's needed only in revision mode, and there its absence is a rendering
    error)."""
    path = Path(prompts_dir) / REVISE_PROMPT
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _read_prompt_inputs(
    step_name: str,
    *,
    prompts_dir: Path,
    templates_dir: Path,
    extra_context_documents: list[str] | None,
) -> tuple[str, str, dict[str, str]]:
    step_dir = Path(prompts_dir) / prompt_dir_name(step_name)
    system_text = (step_dir / "system.md").read_text(encoding="utf-8")
    user_text = (step_dir / "user.md.j2").read_text(encoding="utf-8")

    # A local import breaks the cycle base.py (imports cache.py) <->
    # cache.py (would import base.py at module level).
    from orchestrator.steps import base as steps_base

    context_documents = steps_base.load_context_documents(
        step_name, templates_dir=templates_dir, extra_paths=extra_context_documents
    )
    return system_text, user_text, context_documents


@dataclass
class CacheEntry:
    """The contents of `problems/<problem_id>/.cache/<step>.json`.

    `input_parts`/`prompt_parts` — hashes by parts (`compute_input_parts`/
    `compute_prompt_parts`), only for explaining what changed; `None` for
    entries made before they appeared. `input_values` — the input itself by parts
    (`compute_input_values`): what changed since the last run is shown to the
    model in revision mode; `None` for entries made before it appeared.
    `accepted` — the entry was made by
    `llm accept` (the current files were accepted without a model call), not by
    generation. `conflicts` — part of the model's response wasn't applied: the conflicts
    directory relative to the problem directory (`steps.base._write_conflicts`);
    such an entry isn't a cache hit until the author resolves the conflicts and
    runs `llm accept`.
    """

    input_hash: str
    prompt_hash: str
    model: str
    status: str
    timestamp: str
    input_parts: dict[str, str] | None = None
    prompt_parts: dict[str, str] | None = None
    input_values: dict[str, Any] | None = None
    accepted: bool = False
    conflicts: str | None = None


def _cache_file_path(problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return Path(problems_dir) / problem_id / ".cache" / f"{step_name}.json"


def load_cache_entry(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> CacheEntry | None:
    """Reads the step's cache if the file exists and is a valid CacheEntry.

    A missing file, broken JSON or a structure not matching the format
    are treated the same — as "no cache" (`None`), not as an error: in that
    case the pipeline just calls the model again.
    """
    path = _cache_file_path(problem_id, step_name, problems_dir=problems_dir)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None

    try:
        return CacheEntry(**raw)
    except TypeError:
        return None


def save_cache_entry(
    problem_id: str,
    step_name: str,
    entry: CacheEntry,
    *,
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    """Overwrites `problems/<problem_id>/.cache/<step_name>.json`."""
    path = _cache_file_path(problem_id, step_name, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def is_cache_valid(
    entry: CacheEntry | None,
    current_input_hash: str,
    current_prompt_hash: str,
    current_model: str | None = None,
) -> bool:
    """Whether the model call can be skipped and `entry` reused.

    `uncertain` is never considered a valid cache, regardless of the hashes
    (see CLAUDE.md, "Caching by spec hash", item 3). A change of the step's
    model (`current_model`, the model class from `model_router.STEP_TO_MODEL`)
    also invalidates the cache; `None` — don't compare the model.
    """
    if entry is None:
        return False
    if entry.status == UNCERTAIN_STATUS or entry.conflicts is not None:
        return False
    if current_model is not None and entry.model != current_model:
        return False
    return entry.input_hash == current_input_hash and entry.prompt_hash == current_prompt_hash


@dataclass
class PendingResponse:
    """The contents of `problems/<problem_id>/.cache/<step>.response.json` — a model
    response not yet applied to the problem's files.

    Written right after the model call, before checking the response and writing the files, and
    deleted once the response is applied (or turned out `uncertain`): if the response
    failed the check, the paid call isn't lost — after fixing the
    orchestrator it can be applied again without the model (`llm apply`).

    `cache_entry` — the cache entry saved on a successful application
    (its hashes are the input the response was received for); `revised` — the response was received in
    revision mode (the model was shown the step's current files); `response` —
    the `ModelResponse` fields.
    """

    cache_entry: CacheEntry
    revised: bool
    response: dict[str, Any]


def _pending_response_path(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return Path(problems_dir) / problem_id / ".cache" / f"{step_name}.response.json"


def save_pending_response(
    problem_id: str,
    step_name: str,
    pending: PendingResponse,
    *,
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    path = _pending_response_path(problem_id, step_name, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(pending), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def load_pending_response(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> PendingResponse | None:
    """The step's unapplied response; `None` — there is none (or the file is broken — as in
    `load_cache_entry`)."""
    path = _pending_response_path(problem_id, step_name, problems_dir=problems_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PendingResponse(
            cache_entry=CacheEntry(**raw["cache_entry"]),
            revised=bool(raw["revised"]),
            response=dict(raw["response"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def discard_pending_response(
    problem_id: str, step_name: str, *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    _pending_response_path(problem_id, step_name, problems_dir=problems_dir).unlink(missing_ok=True)


def describe_changed_parts(
    old_parts: dict[str, str] | None, new_parts: dict[str, str]
) -> list[str]:
    """Which parts changed between two sets of per-part hashes: the part's
    name, marked ` (added)`/` (removed)` for those that appeared and vanished.
    `old_parts=None` (a cache entry without parts) — an empty list: there's nothing
    to say, the calling code reports that itself."""
    if old_parts is None:
        return []
    names = [name for name in new_parts if name in old_parts and old_parts[name] != new_parts[name]]
    names += [f"{name} (added)" for name in new_parts if name not in old_parts]
    names += [f"{name} (removed)" for name in old_parts if name not in new_parts]
    return names
