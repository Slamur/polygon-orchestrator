"""Spec references — files of other problems that are attached to the model
call as samples (see docs/SPEC_FORMAT.md, "references").

A reference is a path from the repository root to a file or a directory. A directory reference
is laid out like the orchestrator's result `problems/<problem_id>/` (`validator.cpp`,
`generators/`, ..., `authoring/constraints.yaml`) and possibly with its own
spec `authoring/spec.yaml` — that's how the sample problems `examples/<problem_id>/` are laid out, but
the directory of a past problem of the author in `problems/` fits too (including one
downloaded by `polygon pull`). Each step takes only its own part from the directory
(`REFERENCE_DOCUMENTS`); a file reference is attached as is.

References are shared (`references` at the spec root — for all steps) and
per-step (`references` in a step's section, `STEP_SECTIONS`) — per-step ones
are added to the shared ones, not replacing them.

Errors (`SpecReferenceError`) — before calling the model, so that a typo in a path doesn't
turn into a silently skipped sample:
- the path doesn't exist;
- a per-step directory reference gave this step no files;
- a shared directory reference gave no files to any step.
A shared directory that has no part for a particular step (e.g. a
problem with a standard checker has no `checker.cpp`) is simply
skipped for that step.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from orchestrator.problem_paths import (
    TESTS_PLAN_FILENAME,
    TUTORIAL_FILENAME,
    find_authoring_file,
    is_authoring_name,
    is_removed_mark,
)
from orchestrator.spec import SPEC_FILENAME, ProblemSpec
from orchestrator.statement_languages import read_spec_language

# What each step takes from a directory reference: paths relative to the directory,
# an entry with `*` is a glob (files in sorted order); the spec and
# `constraints.yaml` — from `authoring/` (`problem_paths.find_authoring_file`). `spec.yaml`
# is attached only if at least one other file was taken from the directory —
# a spec without a result doesn't serve as a sample. Files marked by a step as
# deleted (`<name>.removed`) and the tutorial `tutorial.tex` (from the author's draft, not
# a statement sample) don't match the glob. `{language}` — the main statement language
# of the directory reference (`read_spec_language`); `statement/*.tex` — the statement of a
# problem not yet moved into language directories. The test preparation steps
# take "input → result" pairs of their level: `tests_plan` — spec → plan (and
# the script, if the sample has no plan), `generators_draft` — plan → generators,
# `test_script_draft` — plan → script.
REFERENCE_DOCUMENTS: dict[str, list[str]] = {
    "statement_draft": [SPEC_FILENAME, "statement/{language}/*.tex", "statement/*.tex"],
    "constraints_pick": [SPEC_FILENAME, "constraints.yaml", "validator.cpp"],
    "tests_plan": [
        SPEC_FILENAME,
        "constraints.yaml",
        TESTS_PLAN_FILENAME,
        "test_script",
        "test_script_groups",
    ],
    "generators_draft": [TESTS_PLAN_FILENAME, "generators/*.cpp"],
    "test_script_draft": [
        "constraints.yaml",
        TESTS_PLAN_FILENAME,
        "test_script",
        "test_script_groups",
    ],
    "solutions_main": [SPEC_FILENAME, "solutions/*"],
    "solutions_ok": [SPEC_FILENAME, "solutions/*"],
    "solutions_slow": [SPEC_FILENAME, "solutions/*"],
    "solutions_wrong": [SPEC_FILENAME, "solutions/*"],
    "checker_draft": [SPEC_FILENAME, "checker.cpp"],
    "interactor_draft": [SPEC_FILENAME, "interactor.cpp"],
}

# The spec section containing the per-step `references` for the step.
STEP_SECTIONS: dict[str, str] = {
    "statement_draft": "statement_draft",
    "constraints_pick": "constraints",
    "tests_plan": "generation",
    "generators_draft": "generation",
    "test_script_draft": "generation",
    "solutions_main": "solutions",
    "solutions_ok": "solutions",
    "solutions_slow": "solutions",
    "solutions_wrong": "solutions",
    "checker_draft": "checker",
    "interactor_draft": "interactor",
}


class SpecReferenceError(ValueError):
    """A spec reference isn't found or gives the step nothing."""


def step_references(step_name: str, spec: ProblemSpec) -> tuple[list[str], list[str]]:
    """(shared, per-step) references for the step `step_name`, as written in the spec."""
    section = getattr(spec, STEP_SECTIONS[step_name], None)
    section_refs = (section.references if section is not None else None) or []
    return list(spec.references or []), list(section_refs)


def reference_documents(
    step_name: str,
    spec: ProblemSpec,
    *,
    keep: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """The paths of the files the spec references give the step `step_name` — first
    the shared references, then the per-step ones, in the order they are written in the spec; `None` if
    there's nothing (the step works without samples).

    `keep` — an additional filter by the path inside a directory reference
    (e.g. solutions only in the ordered languages); it doesn't apply to file references and
    `spec.yaml`.
    """
    common_refs, section_refs = step_references(step_name, spec)
    paths: list[str] = []

    for ref in common_refs:
        ref_path = _existing(ref, "references")
        if ref_path.is_dir():
            if not any(_directory_files(ref_path, step) for step in REFERENCE_DOCUMENTS):
                raise SpecReferenceError(
                    f"references: directory '{ref}' has none of the files any step "
                    "takes from a reference (see REFERENCE_DOCUMENTS in "
                    "orchestrator/references.py)"
                )
            paths.extend(_directory_files(ref_path, step_name, keep))
        else:
            paths.append(str(ref_path))

    section_name = STEP_SECTIONS[step_name]
    for ref in section_refs:
        ref_path = _existing(ref, f"{section_name}.references")
        if ref_path.is_dir():
            files = _directory_files(ref_path, step_name, keep)
            if not files:
                raise SpecReferenceError(
                    f"{section_name}.references: directory '{ref}' has no files for "
                    f"step '{step_name}' (expected any of "
                    f"{[e for e in REFERENCE_DOCUMENTS[step_name] if e != SPEC_FILENAME]})"
                )
            paths.extend(files)
        else:
            paths.append(str(ref_path))

    return paths or None


def _existing(ref: str, field: str) -> Path:
    path = Path(ref)
    if not path.exists():
        raise SpecReferenceError(
            f"{field}: '{ref}' not found (paths are relative to the repository root)"
        )
    return path


def _directory_files(
    directory: Path, step_name: str, keep: Callable[[str], bool] | None = None
) -> list[str]:
    """The files of a directory reference for the step: by `REFERENCE_DOCUMENTS[step_name]`,
    `spec.yaml` — only if there's something besides it."""
    spec_file: Path | None = None
    files: list[Path] = []
    for entry in REFERENCE_DOCUMENTS[step_name]:
        entry = entry.format(language=read_spec_language(directory)) if "{" in entry else entry
        if entry == SPEC_FILENAME:
            candidate = find_authoring_file(directory, SPEC_FILENAME)
            spec_file = candidate if candidate.is_file() else None
            continue
        if is_authoring_name(entry):
            candidate = find_authoring_file(directory, entry)
            matches = [candidate] if candidate.is_file() else []
        elif "*" in entry:
            matches = sorted(
                path
                for path in directory.glob(entry)
                if path.is_file() and not is_removed_mark(path) and path.name != TUTORIAL_FILENAME
            )
        else:
            matches = [directory / entry] if (directory / entry).is_file() else []
        files.extend(
            path for path in matches if keep is None or keep(path.relative_to(directory).as_posix())
        )
    if not files:
        return []
    return [str(path) for path in ([spec_file] if spec_file else []) + files]
