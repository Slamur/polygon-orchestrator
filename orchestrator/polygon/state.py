"""The state of the `problem_id` -> Polygon problemId binding.

Stored in `problems/<problem_id>/polygon_state.json` — at the level of
`problems/<problem_id>/`, not inside `.cache/`: it isn't the result cache of a
model step, but a fact about the state on the Polygon side.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orchestrator.problem_paths import POLYGON_STATE_FILENAME, PROBLEMS_DIR

# Fields of the old format that are no longer used and are discarded on reading:
# `steps` — a record of "what the step last sent" as one hash
# per step, before `revision`/`synced` appeared.
_LEGACY_FIELDS = ("steps",)


@dataclass
class PolygonState:
    """The contents of `problems/<problem_id>/polygon_state.json`."""

    problem_id: str
    polygon_id: int
    created_at: str  # ISO8601 UTC
    # The sync base — see `orchestrator/polygon/sync.py`: the revision on
    # Polygon that the `synced` records matched (`None` — unknown), and
    # the key of a problem element -> the sha256 of its contents in this revision.
    revision: int | None = None
    synced: dict[str, str] = field(default_factory=dict)
    # A key from `synced` -> the stamp of the version of the file on Polygon that
    # its hash corresponds to (`sync.file_stamp`: the name, the modification time,
    # the length). Only files have it and only paired with `synced` — see
    # `update_sync`; there's no stamp without a hash.
    stamps: dict[str, str] = field(default_factory=dict)


def _state_file_path(problem_id: str, *, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return Path(problems_dir) / problem_id / POLYGON_STATE_FILENAME


def load_polygon_state(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR
) -> PolygonState | None:
    """Reads the state if the file exists and is a valid PolygonState.

    A missing file, broken JSON or a structure not matching the format
    are treated the same — as "no state" (`None`), not as an error.
    A file of the old format (with `steps`, without `revision`/`synced`) is read: its sync
    base is just unknown.
    """
    path = _state_file_path(problem_id, problems_dir=problems_dir)
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    for legacy in _LEGACY_FIELDS:
        raw.pop(legacy, None)

    try:
        state = PolygonState(**raw)
    except TypeError:
        return None
    if not isinstance(state.synced, dict) or not isinstance(state.stamps, dict):
        return None
    if state.revision is not None and not isinstance(state.revision, int):
        return None
    return state


def save_polygon_state(
    problem_id: str, state: PolygonState, *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    """Overwrites `problems/<problem_id>/polygon_state.json` atomically:
    via a temporary file next to it and `os.replace` — an interrupted write doesn't
    leave truncated JSON (`load_polygon_state` would consider it
    the absence of a binding)."""
    path = _state_file_path(problem_id, problems_dir=problems_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(asdict(state), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def update_sync(
    problem_id: str,
    *,
    synced: dict[str, str | None],
    revision: int | None,
    stamps: dict[str, str | None] | None = None,
    replace: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
) -> None:
    """Updates the sync base in `polygon_state.json`: the keys from `synced`
    are overwritten (`None` — the key is deleted), `revision` is replaced.
    `replace` — the base is entirely replaced with `synced` (the other keys
    are deleted).

    A stamp (`stamps`) is written only together with the hash of the same key: for
    each key from `synced` the stamp is replaced with `stamps[key]`, and if
    it isn't there (or the key is being deleted) — it's deleted. This way the stamp and the hash always
    describe the same version of the file on Polygon, even if the write
    was interrupted halfway (pull failed on some of the files): an upload without
    a stamp (push) removes the old one.

    Requires the state to already exist (the problem is bound by the
    `create_problem` step or `polygon pull`) — otherwise `ValueError`.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    stamps = stamps or {}
    if replace:
        state.synced, state.stamps = {}, {}
    for key, value in synced.items():
        stamp = stamps.get(key)
        if value is None:
            state.synced.pop(key, None)
            state.stamps.pop(key, None)
            continue
        state.synced[key] = value
        if stamp is None:
            state.stamps.pop(key, None)
        else:
            state.stamps[key] = stamp
    state.revision = revision
    save_polygon_state(problem_id, state, problems_dir=problems_dir)


def record_stamps(
    problem_id: str, stamps: dict[str, str], *, problems_dir: Path = PROBLEMS_DIR
) -> None:
    """Writes the `stamps` to keys that already have a hash in the base
    (the rest are discarded), changing neither the hashes nor the revision. The caller
    is responsible for the stamps being taken from the same Polygon revision as the
    `revision` in the base (see `pipeline._record_stamps`)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise ValueError(f"'{problem_id}': polygon_state.json is missing or corrupted")
    state.stamps.update({key: value for key, value in stamps.items() if key in state.synced})
    save_polygon_state(problem_id, state, problems_dir=problems_dir)
