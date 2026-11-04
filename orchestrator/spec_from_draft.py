"""A problem's spec from the author's draft — `orchestrator <problem> init --draft NAME`
(CLAUDE.md, "Running").

The draft (`orchestrator/drafts.py`) is a free-form description of the problem.
The model (`STEP_NAME` in `model_router.py`) carries it into a spec in the
`docs/SPEC_FORMAT.md` format: what the author wrote it keeps, and what's missing
it fills in itself and may add its own ideas (solutions, contestant mistakes,
tests) — each such value is marked in the spec with a
`# PROPOSED, REVIEW ME: ...` or `# SUGGESTION: ...` comment and a note in the response
(`prompts/spec_from_draft/system.md`). The spec's base is the
`templates/spec_template.yaml` template or, with `--ref`, the base problem's spec
(`spec.prepare_spec_init`): then the draft describes how the new problem
differs from it.

This isn't a generative pipeline step: no cache and no `llm apply`. The model's response
is written to `spec.yaml` in any case if it contains a spec (even one not
passing validation or with `status: uncertain` — it's a draft the
author reads through anyway), so that the call isn't wasted. The `problem_id` and the
reference to the base problem are set by the orchestrator itself, on top of the model's response.
The draft copy goes next to the spec, into the problem directory's `authoring/`
(`drafts.problem_draft_name`).

If the problem already has a spec, `init --draft` revises it, like
`llm generate` revises a step's files (`prepare_spec_revision`): the model gets the
current spec (possibly with the author's manual edits) and what changed in the
draft — the previous draft copy in `authoring/` against the new one — via the shared
block `prompts/revise.md.j2`. The draft didn't change — the model isn't
called (`draft_unchanged`). Previous versions of the spec and the draft copy —
in `.llm_backup/<time>/`.

After writing, `init --draft` runs the critic (`orchestrator/spec_critic.py`); its
findings on the values the model proposed come back here — `revise_spec_by_findings`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator import model_router
from orchestrator.drafts import problem_draft_name
from orchestrator.problem_paths import (
    EXAMPLES_DIR,
    SPEC_FILENAME,
    authoring_dir,
    find_authoring_file,
    is_draft_name,
    problem_address,
    problem_id_for,
    problem_paths,
)
from orchestrator.spec import (
    SPEC_TEMPLATE,
    SpecInit,
    SpecInitError,
    add_reference,
    set_problem_id,
    spec_path,
    spec_validation_errors,
    write_spec,
)
from orchestrator.steps.base import (
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_UNCERTAIN,
    TEMPLATES_DIR,
    Backup,
    StepRevision,
    read_system_prompt,
    render_revision_prompt,
    render_user_prompt,
)

STEP_NAME = "spec_from_draft"
SPEC_ARTIFACT = SPEC_FILENAME

# The spec format for the model — relative to the directory containing
# `templates/` (the repository root).
SPEC_FORMAT_DOC = Path("docs") / "SPEC_FORMAT.md"

_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}

# Under which name the draft is part of the input in the "what changed" block
# (`prompts/revise.md.j2`).
DRAFT_INPUT = "draft"


@dataclass
class SpecFromDraftResult:
    """The result of `init --draft`. `path` — the created or revised spec, `None`
    — the model returned no spec and nothing was written. `unchanged` — during a
    revision the model left the spec as is (`path` — the same one, not
    overwritten). `validation_errors` — the spec's validation errors
    (`spec.load_spec`); an empty list — the spec is valid. `backup_dir` —
    `.llm_backup/<time>/` with the previous versions of the spec and the draft copy."""

    status: str
    notes: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None
    draft_copy: Path | None = None
    unchanged: bool = False
    validation_errors: list[str] = field(default_factory=list)
    backup_dir: Path | None = None
    # The critic's run (`spec_critic.CritiqueOutcome`); `None` — the critic didn't run.
    critique: Any = None


@dataclass
class SpecRevisionByFindings:
    """The generator's answer to the critic's findings (`revise_spec_by_findings`).
    `spec_text` — the revised spec with the `problem_id` and the references set,
    `None` — the model left the spec as is or returned none."""

    status: str
    spec_text: str | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DraftRevision:
    """Revising an existing spec by a changed draft.

    `previous_draft` — the text of the previous draft copy from `authoring/` from
    which the spec was written; `None` — there's no copy (or there are several), what
    changed is unknown. `previous_copies` — these copies: on writing they
    are replaced with a copy of the new draft (the extension may differ)."""

    previous_draft: str | None
    previous_copies: list[Path] = field(default_factory=list)


def prepare_spec_revision(
    problem_path: str, *, problems_dir: Path
) -> tuple[SpecInit, DraftRevision]:
    """The base for revising the spec of the problem `problems_dir/<problem_path>/`: the current
    spec (`SpecInit.base_text`, without a base problem — the references are already in it) and
    the previous draft copy. No spec — `SpecInitError`."""
    path = spec_path(problem_path, problems_dir=problems_dir)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecInitError(f"failed to read {path}: {exc}") from exc
    directory = authoring_dir(Path(problems_dir) / problem_path)
    copies = sorted(p for p in directory.iterdir() if p.is_file() and is_draft_name(p.name))
    previous = copies[0].read_text(encoding="utf-8") if len(copies) == 1 else None
    init = SpecInit(
        problem_id=problem_id_for(problems_dir, problem_path), path=path, base_text=current
    )
    return init, DraftRevision(previous_draft=previous, previous_copies=copies)


def draft_unchanged(revision: DraftRevision, draft_path: Path) -> bool:
    """The draft matches the previous copy from which the spec was written."""
    return revision.previous_draft == Path(draft_path).read_text(encoding="utf-8")


def context_documents(
    *,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
    skip_reference: str | None = None,
) -> dict[str, str]:
    """Documents for the model: the spec format (`SPEC_FORMAT_DOC`), the template
    (`SPEC_TEMPLATE`) and the filled-in specs of the sample problems — samples of what
    a finished spec looks like. The spec of the base problem `skip_reference` is already in the
    user prompt and isn't attached a second time."""
    templates_dir = Path(templates_dir)
    documents = {
        SPEC_FORMAT_DOC.as_posix(): (templates_dir.parent / SPEC_FORMAT_DOC).read_text(
            encoding="utf-8"
        ),
        SPEC_TEMPLATE: (templates_dir / SPEC_TEMPLATE).read_text(encoding="utf-8"),
    }
    for example in problem_paths(examples_dir):
        address = problem_address(examples_dir, example)
        path = find_authoring_file(Path(examples_dir) / example, SPEC_FILENAME)
        if address != skip_reference and path.is_file():
            documents[f"{address}/{SPEC_FILENAME}"] = path.read_text(encoding="utf-8")
    return documents


def _note(field_name: str, kind: str, explanation: str) -> dict[str, Any]:
    return {"field": field_name, "kind": kind, "explanation": explanation}


def _finalize_spec_text(init: SpecInit, text: str) -> tuple[str, list[dict[str, Any]]]:
    """The `problem_id` and the reference to the base problem — on top of the model's response: they're
    set by the orchestrator, not the model. If it didn't work (e.g. the response isn't
    YAML) — the text as is and a note."""
    notes: list[dict[str, Any]] = []
    try:
        text = set_problem_id(text, init.problem_id)
    except SpecInitError as exc:
        notes.append(_note("problem_id", "uncertain", f"not set by the orchestrator: {exc}"))
    if init.reference is not None:
        try:
            text = add_reference(text, init.reference)
        except SpecInitError as exc:
            notes.append(_note("references", "uncertain", f"'{init.reference}' not added: {exc}"))
    if not text.endswith("\n"):
        text += "\n"
    return text, notes


def _call_model(
    init: SpecInit,
    system_prompt: str,
    user_prompt: str,
    *,
    templates_dir: Path,
    examples_dir: Path,
):
    response = model_router.call_model(
        STEP_NAME,
        system_prompt,
        user_prompt,
        context_documents=context_documents(
            templates_dir=templates_dir, examples_dir=examples_dir, skip_reference=init.reference
        ),
    )
    if response.status not in _VALID_STATUSES:
        raise ValueError(
            f"'{STEP_NAME}': the model returned an unknown status "
            f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
        )
    return response


def generate_spec_from_draft(
    init: SpecInit,
    draft_path: Path,
    *,
    revision: DraftRevision | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> SpecFromDraftResult:
    """Creates the spec `init.path` from the draft `draft_path` (see the module
    docstring). `init` — the already checked location and base of the spec
    (`spec.prepare_spec_init`), `draft_path` — a non-empty draft
    (`drafts.find_draft`). With `revision` — revises the existing spec
    `init.path` (`prepare_spec_revision`)."""
    draft_text = Path(draft_path).read_text(encoding="utf-8")

    system_prompt = read_system_prompt(STEP_NAME, prompts_dir=prompts_dir)
    user_prompt = render_user_prompt(
        STEP_NAME,
        {
            "problem_id": init.problem_id,
            "draft": draft_text.rstrip("\n"),
            "base_spec": init.base_text.rstrip("\n"),
            "reference": init.reference,
            "revision": revision is not None,
            "findings": None,
        },
        prompts_dir=prompts_dir,
    )
    if revision is not None:
        step_revision = StepRevision(
            current_paths={SPEC_ARTIFACT: init.path},
            previous_input_values=None
            if revision.previous_draft is None
            else {DRAFT_INPUT: revision.previous_draft.rstrip("\n")},
        )
        user_prompt = (
            user_prompt.rstrip("\n")
            + "\n\n"
            + render_revision_prompt(
                step_revision, {DRAFT_INPUT: draft_text.rstrip("\n")}, prompts_dir=prompts_dir
            )
        )
    response = _call_model(
        init, system_prompt, user_prompt, templates_dir=templates_dir, examples_dir=examples_dir
    )

    notes = list(response.notes)
    result = SpecFromDraftResult(status=response.status, notes=notes)
    spec_text = response.artifacts.get(SPEC_ARTIFACT)
    for filename in sorted(set(response.artifacts) - {SPEC_ARTIFACT}):
        notes.append(_note(filename, "uncertain", "unexpected file in the response, ignored"))
    if revision is not None:
        for entry in [*response.removed, *response.renamed]:
            notes.append(
                _note(SPEC_ARTIFACT, "uncertain", f"ignored removal/rename of the spec: {entry}")
            )
    returned = spec_text is not None and bool(spec_text.strip())
    result.unchanged = revision is not None and not returned and SPEC_ARTIFACT in response.unchanged
    if not returned and not result.unchanged:
        if response.status != STATUS_UNCERTAIN:
            kept = ", kept as is" if revision is not None else ""
            notes.append(_note(SPEC_ARTIFACT, "uncertain", f"the model returned no spec{kept}"))
        return result

    backup = Backup(init.path.parent.parent)
    if not result.unchanged:
        spec_text, finalize_notes = _finalize_spec_text(init, spec_text)
        notes.extend(finalize_notes)
        if init.path.is_file() and init.path.read_text(encoding="utf-8") != spec_text:
            backup.save(init.path)
        write_spec(init, spec_text)
    result.path = init.path
    result.draft_copy = init.path.parent / problem_draft_name(draft_path)
    for copy in [] if revision is None else revision.previous_copies:
        if copy != result.draft_copy or copy.read_text(encoding="utf-8") != draft_text:
            backup.save(copy)
        if copy != result.draft_copy:
            copy.unlink()
    shutil.copyfile(draft_path, result.draft_copy)
    result.backup_dir = backup.directory
    result.validation_errors = spec_validation_errors(result.path, expected_id=init.problem_id)
    return result


def revise_spec_by_findings(
    init: SpecInit,
    draft_text: str,
    spec_text: str,
    findings: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> SpecRevisionByFindings:
    """One revision round by the critic's findings (`orchestrator/spec_critic.py`):
    the model gets the draft, the current spec `spec_text` and `findings` (YAML) and
    applies each finding or keeps the value with a reason in `notes`. Nothing is
    written: the critic checks the result before writing it."""
    system_prompt = read_system_prompt(STEP_NAME, prompts_dir=prompts_dir)
    user_prompt = render_user_prompt(
        STEP_NAME,
        {
            "problem_id": init.problem_id,
            "draft": draft_text.rstrip("\n"),
            "base_spec": spec_text.rstrip("\n"),
            "reference": None,
            "revision": True,
            "findings": findings.rstrip("\n"),
        },
        prompts_dir=prompts_dir,
    )
    response = _call_model(
        init, system_prompt, user_prompt, templates_dir=templates_dir, examples_dir=examples_dir
    )
    result = SpecRevisionByFindings(status=response.status, notes=list(response.notes))
    for filename in sorted(set(response.artifacts) - {SPEC_ARTIFACT}):
        result.notes.append(
            _note(filename, "uncertain", "unexpected file in the response, ignored")
        )
    revised = response.artifacts.get(SPEC_ARTIFACT)
    if revised is not None and revised.strip():
        result.spec_text, finalize_notes = _finalize_spec_text(init, revised)
        result.notes.extend(finalize_notes)
    return result
