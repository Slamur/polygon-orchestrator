"""Incremental sync with Polygon: what counts as "the same".

`polygon_state.json` stores the sync base (like `origin/...` in git):

- `revision` — the problem's revision on Polygon that the local base
  last matched (after a `polygon push` commit or after
  `polygon pull`); `None` — unknown (the problem was created/bound before
  this field appeared or hasn't been synced even once);
- `synced` — the key of a problem element -> the sha256 of its contents in this revision.

`polygon push` uploads only the elements whose current local hash
differs from `synced` — and only if the revision on Polygon still equals
`revision`, and the working copy has no uncommitted edits (otherwise Polygon
was changed bypassing the orchestrator — see `orchestrator/polygon/pipeline.py`). `polygon pull`
rewrites the base by what's on Polygon now.

The key is the file's path relative to `problems/<problem_id>/` (or
`templates/` for `problem_lib.h`), except for a few "non-files":
the TL/ML, the name of the assigned validator/checker, the problem's name in the statement of
each language.
The hash is always computed from what lies/would lie locally — so
`push` and `pull` get the same values for the same contents.

`stamps` — for files that Polygon returns in a listing with a modification
time (`problem.files`, `problem.solutions`; see `remote_stamps`):
key -> the stamp of the version of the file on Polygon that the key's hash
in `synced` corresponds to. The stamp in the listing matched — the file on Polygon hasn't
changed since, and `polygon pull` doesn't download it but takes the hash from the base. The stamp
is written by pull (together with the hash) and by push at the end of the run (`pipeline.py`).
An extra mismatch (the file was re-saved without changes) costs only a
download; a match with a changed file doesn't happen as long as Polygon
updates the modification time on every edit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    CONSTRAINTS_FILENAME,
    SPEC_FILENAME,
    TESTS_PLAN_FILENAME,
    is_authoring_name,
    is_draft_name,  # noqa: F401 — part of the module's interface (pull, upload_attachments)
)

LIMITS_KEY = "limits"
PROBLEM_LIB_KEY = "problem_lib.h"
VALIDATOR_FILE_KEY = "validator.cpp"
VALIDATOR_KEY = "validator"  # the name of the file assigned as the validator
CHECKER_FILE_KEY = "checker.cpp"
CHECKER_KEY = "checker"  # the name of the assigned checker (`checker.cpp` or `std::...`)
INTERACTIVE_KEY = "interactive"  # the problem's interactivity flag (`problem.updateInfo`)
INTERACTOR_FILE_KEY = "interactor.cpp"
INTERACTOR_KEY = "interactor"  # the name of the assigned interactor
SCRIPT_KEY = "test_script"  # one per testset, whatever the local file
# Attachments are the `authoring/` files of the problem directory; on Polygon and in the base — by
# file name, without the directory.
SPEC_KEY = SPEC_FILENAME
CONSTRAINTS_KEY = CONSTRAINTS_FILENAME  # the whole file; the problem's TL/ML — `LIMITS_KEY`
TESTS_PLAN_KEY = TESTS_PLAN_FILENAME

# The `constraints.yaml` header written by previous versions of `polygon pull`
# when Polygon has only TL/ML (now the TL/ML are in `limits.yaml`): such a
# file isn't the result of `constraints_pick`, and `upload_attachments` doesn't
# upload it.
PULLED_CONSTRAINTS_HEADER = (
    "# Pulled from Polygon (orchestrator <problem> polygon pull): TL/ML only.\n"
    "# This is NOT a constraints_pick result — there are no variable ranges,\n"
    "# test groups or validator section here. Steps that take constraints.yaml\n"
    "# as input (tests_plan and the steps after it) will see only this.\n"
)

# The local names of the validator, the checker and the interactor: source files with such
# names aren't generators, even if not assigned (see `generator_source_names`).
VALIDATOR_FILENAME = "validator.cpp"
CHECKER_FILENAME = "checker.cpp"
INTERACTOR_FILENAME = "interactor.cpp"
STANDARD_CHECKER_PREFIX = "std::"


def is_attachment_name(name: str) -> bool:
    """An aux file on Polygon that stores an `authoring/` file of the problem directory
    (the `upload_attachments` step): the spec, `constraints.yaml`, `tests_plan.yaml`,
    the draft copy.
    The key in the sync base is the name itself."""
    return is_authoring_name(name)


def statement_file_key(filename: str, language: str) -> str:
    """The key of a statement file in the language `language` (a code, `ru`) — as the file lies
    locally: `statement/<code>/<file>`."""
    return f"statement/{language}/{filename}"


def statement_name_key(language: str) -> str:
    """The key of the problem's name in the statement in the language `language` (`name.txt`)."""
    return f"statement/{language}/name"


def example_key(number: int) -> str:
    return f"statement/examples/example_{number}.txt"


def dialogue_keys(number: int) -> tuple[str, str]:
    """The keys of an interactive problem's example dialogue (input/output for statement)."""
    return (
        f"statement/examples/example_{number}.input_for_statement.txt",
        f"statement/examples/example_{number}.output_for_statement.txt",
    )


def verify_key(number: int) -> str:
    """The key of an example's "verify input/output for statements" flag."""
    return f"statement/examples/example_{number}.verify_for_statement.txt"


def interactive_digest(interactive: bool) -> str:
    return digest(f"interactive={str(interactive).lower()}")


def generator_key(name: str) -> str:
    return f"generators/{name}"


def solution_key(name: str) -> str:
    return f"solutions/{name}"


def generator_source_names(
    source_files: list[dict],
    validator: str | None,
    checker: str | None,
    interactor: str | None = None,
) -> list[str]:
    """The names of the generators among the problem's source files on Polygon. A heuristic:
    Polygon doesn't distinguish generators from other source files, so a
    generator is any `*.cpp` except the validator, the checker and the interactor
    (assigned or with the local names `validator.cpp`/`checker.cpp`/
    `interactor.cpp`)."""
    not_generators = {
        VALIDATOR_FILENAME,
        CHECKER_FILENAME,
        INTERACTOR_FILENAME,
        validator,
        checker,
        interactor,
    }
    return sorted(
        file["name"]
        for file in source_files
        if file["name"].endswith(".cpp") and file["name"] not in not_generators
    )


# "Non-file" keys -> how to show them to the author (`describe_keys`).
_KEY_LABELS = {
    LIMITS_KEY: "TL/ML (limits.yaml)",
    VALIDATOR_KEY: "validator assignment",
    CHECKER_KEY: "checker assignment",
    INTERACTOR_KEY: "interactor assignment",
    INTERACTIVE_KEY: "interactive flag",
    SCRIPT_KEY: "test script",
}
# Assignment -> the file on whose upload the assignment isn't shown separately.
_ASSIGNED_FILES = {
    VALIDATOR_KEY: VALIDATOR_FILE_KEY,
    CHECKER_KEY: CHECKER_FILE_KEY,
    INTERACTOR_KEY: INTERACTOR_FILE_KEY,
}


def describe_keys(keys: Iterable[str]) -> list[str]:
    """Base keys -> names for the CLI output, in the order of the keys: files — by their path in the
    problem directory (Attachments — with `authoring/`), the rest — in words.
    The assignment of the validator/checker/interactor together with its file is one
    line, the file."""
    keys = list(keys)
    return [
        _KEY_LABELS.get(key)
        or (f"{AUTHORING_DIRNAME}/{key}" if is_attachment_name(key) else _statement_label(key))
        for key in keys
        if _ASSIGNED_FILES.get(key) not in keys
    ]


def _statement_label(key: str) -> str:
    """The name in the statement (`statement_name_key`) — as the `name.txt` file;
    the other keys are paths themselves."""
    parts = key.split("/")
    if len(parts) == 3 and parts[0] == "statement" and parts[2] == "name":
        return f"{key}.txt"
    return key


def is_stamped_key(key: str) -> bool:
    """Whether the key has a stamp in the Polygon listing (see `remote_stamps`)."""
    return (
        key
        in (
            PROBLEM_LIB_KEY,
            VALIDATOR_FILE_KEY,
            CHECKER_FILE_KEY,
            INTERACTOR_FILE_KEY,
        )
        or is_attachment_name(key)
        or key.startswith(("generators/", "solutions/"))
    )


def file_stamp(file: dict) -> str | None:
    """The stamp of a file's/solution's version on Polygon (the `File`/`Solution` objects):
    the name, the modification time, the length. `None` — Polygon didn't return the time."""
    mtime = file.get("modificationTimeSeconds")
    if mtime is None:
        return None
    return f"{file['name']}:{mtime}:{file.get('length')}"


def remote_stamps(
    files: dict,
    solutions: list[dict],
    validator: str | None,
    checker: str | None,
    interactor: str | None = None,
) -> dict[str, str]:
    """Base key -> the stamp of the current version on Polygon, from the responses of
    `problem.files`, `problem.solutions`, `problem.validator`,
    `problem.checker`, `problem.interactor` (only for an interactive problem)
    — for all files pull compares."""
    sources = {file["name"]: file for file in files.get("sourceFiles") or []}
    by_key: dict[str, dict] = {}
    for file in files.get("resourceFiles") or []:
        if file["name"] == PROBLEM_LIB_KEY:
            by_key[PROBLEM_LIB_KEY] = file
    for file in files.get("auxFiles") or []:
        if is_attachment_name(file["name"]):
            by_key[file["name"]] = file
    if validator in sources:
        by_key[VALIDATOR_FILE_KEY] = sources[validator]
    if checker in sources and not checker.startswith(STANDARD_CHECKER_PREFIX):
        by_key[CHECKER_FILE_KEY] = sources[checker]
    if interactor in sources:
        by_key[INTERACTOR_FILE_KEY] = sources[interactor]
    for name in generator_source_names(list(sources.values()), validator, checker, interactor):
        by_key[generator_key(name)] = sources[name]
    for solution in solutions:
        by_key[solution_key(solution["name"])] = solution
    stamps = {key: file_stamp(file) for key, file in by_key.items()}
    return {key: stamp for key, stamp in stamps.items() if stamp is not None}


def fetch_remote_stamps(
    client: PolygonClient, polygon_id: int, *, interactive: bool = False
) -> dict[str, str]:
    """`remote_stamps` for the problem `polygon_id` — four requests, for an
    interactive problem five (`problem.interactor` isn't requested for a normal problem:
    it has no interactor)."""
    params = {"problemId": str(polygon_id)}
    return remote_stamps(
        client.call("problem.files", params) or {},
        client.call("problem.solutions", params) or [],
        client.call("problem.validator", params),
        client.call("problem.checker", params),
        client.call("problem.interactor", params) if interactive else None,
    )


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def limits_digest(time_limit_ms: int, memory_limit_mb: int) -> str:
    return digest(f"timeLimit={time_limit_ms};memoryLimit={memory_limit_mb}")


@dataclass(frozen=True)
class PolygonRevision:
    """The problem's revision on Polygon (the fields of the `Problem` object)."""

    revision: int
    modified: bool  # there are uncommitted edits in the working copy


def fetch_revision(client: PolygonClient, polygon_id: int) -> PolygonRevision:
    """The current revision of the problem `polygon_id` — one `problems.list` call."""
    found = [
        item
        for item in client.call("problems.list", {"id": str(polygon_id)}) or []
        if item.get("id") == polygon_id
    ]
    if not found:
        raise PolygonStepError(f"Polygon problem id={polygon_id} is not available to this API key")
    problem = found[0]
    return PolygonRevision(
        revision=int(problem.get("revision", 0)), modified=bool(problem.get("modified"))
    )
