"""Step 4 — drafts of the author solutions (ok/wa/tl), CLAUDE.md, "Role of each
generative step", item 4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec, split_solution_stem
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    INTERACTIVE_TUTORIAL,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    artifact_filename,
    constraints_without_limits,
    directory_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)

STEP_NAME = "solutions_draft"

# Optional[list[...]] fields of the solutions section over which user.md.j2 does
# {% for %} without a null check (see base.with_default_lists).
_LIST_FIELDS = ["alternative_approaches", "known_wrong_approaches"]

# The solutions directory inside a directory reference, see `extra_context_documents`.
SOLUTIONS_SUBDIR = "solutions"

# Solution skeletons in `templates/` — `<verdict>_<description>.<language>`
# (`ok.cpp`, `tl_ok_slowio.java`): input/output and the solution structure without
# an algorithm; the skeleton's language is its extension (as in solution names, it matches
# the language), see `extra_context_documents`.
SOLUTION_TEMPLATES_SUBDIR = "solutions"

# `solutions.languages` are free strings; we map common
# spellings to how the language is named in the template file names.
_LANGUAGE_ALIASES = {"c++": "cpp", "python": "py", "python3": "py"}


def _require_solutions_section(problem_id: str, spec: ProblemSpec) -> None:
    if spec.solutions is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': the spec has no "
            "'solutions' section — solution drafts not requested, the step is not run "
            "(see SPEC_FORMAT.md, section 4 — optional)"
        )


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """The spec sections for rendering the prompt AND for the cache hash input — a single
    assembly point, so that they don't diverge (CLAUDE.md, "Caching by spec hash"):
    the `solutions` section, `constraints.intended_complexity` and the statement from
    `statement_draft` entirely in its input/output part (`formal_input_sketch`,
    `formal_output_sketch`, `indexing`, examples with answers, the legend —
    `base.statement_io_section`). Without the last one the model has nowhere to get
    its problem's input/output format from, except the format of the sample problem from
    `spec.yaml`.
    """
    return {
        "solutions": with_default_lists(
            spec.solutions.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "constraints": {"intended_complexity": spec.constraints.intended_complexity},
        "statement": statement_io_section(spec, include_output_and_legend=True),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model, see `statement_draft.compute_input_hash`
    and CLAUDE.md, "Caching by spec hash". Like `run_step`, it requires
    the `solutions` section in the spec and `upstream_artifacts["constraints.yaml"]`.
    The input — `_build_context_sections` + the contents of `constraints.yaml` without
    TL/ML (`base.constraints_without_limits`).
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """What goes into the step's input hash: `(spec section, upstream artifacts)` —
    the shared source for `compute_input_hash` and the per-part hashes
    (`cache.compute_input_values`), so that they don't diverge."""
    _require_solutions_section(problem_id, spec)
    constraints_yaml = constraints_without_limits(
        require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    )
    return _build_context_sections(spec), {CONSTRAINTS_ARTIFACT: constraints_yaml}


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The directory with the solution drafts — the model chooses the exact list of files
    itself (it depends on `solutions.wanted_verdicts`), so unlike
    the other steps there's no fixed file name here: `pipeline.py`
    considers the cache valid only if the directory exists and isn't empty.
    """
    return Path(problems_dir) / problem_id / "solutions"


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files on disk: artifact name (as the
    model would return it) -> path — for revision mode (`base.StepRevision`). All files of
    `solutions/`, except those marked as removed."""
    return directory_artifacts(Path(problems_dir) / problem_id / SOLUTIONS_SUBDIR)


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `solutions.references`, see
    `orchestrator/references.py`). From the `solutions/` of a directory reference only
    the solutions in the languages ordered in `solutions.languages` are taken — a sample of
    the solution structure and input/output for each language (see
    `prompts/solutions_draft/system.md`). A solution's language is the second component
    of the file name by the `<verdict>_<language>_...` scheme (`ok_cpp_prefs_draft.cpp`
    -> `cpp`); solutions in languages not ordered aren't attached, so as not to
    pull someone else's input/output style into the context.

    For an ordered language for which the references gave no
    `ok` solution, all solution skeletons of this
    language from `templates/solutions/*.<language>` (if any) are attached instead of a sample: the way
    the author wants to see input/output in this language is set even without
    references. The skeletons go before the reference files. For an interactive problem
    the `INTERACTIVE_TUTORIAL` rules are attached first (as for
    `statement_draft`). If there's no `solutions` section — `None`: the step
    doesn't run anyway.
    """
    if spec.solutions is None:
        return None

    languages = {
        _LANGUAGE_ALIASES.get(lang.lower(), lang.lower()) for lang in spec.solutions.languages
    }

    def keep(rel_path: str) -> bool:
        if not rel_path.startswith(f"{SOLUTIONS_SUBDIR}/"):
            return True
        return _solution_parts(Path(rel_path))[1] in languages

    rules = [str(Path(templates_dir) / INTERACTIVE_TUTORIAL)] if spec.interactive else []
    references = reference_documents(STEP_NAME, spec, keep=keep) or []
    covered = {
        language
        for verdict, language in map(_solution_parts, map(Path, references))
        if verdict == "ok"
    }
    templates = [
        str(path)
        for language in sorted(languages - covered)
        for path in sorted((Path(templates_dir) / SOLUTION_TEMPLATES_SUBDIR).glob(f"*.{language}"))
    ]
    return [*rules, *templates, *references] or None


def _solution_parts(path: Path) -> tuple[str | None, str | None]:
    """(verdict, language) by a solution's name `<verdict>_<language>_...`
    (`tl_ok_java_slowio_draft.java` -> `("tl_ok", "java")`); `None` where
    the name doesn't follow the scheme."""
    parts = split_solution_stem(path.stem)
    return (parts[0] if parts else None), (parts[1] if len(parts) >= 2 else None)


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
    *,
    revision: StepRevision | None = None,
    saved_response: ModelResponse | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Runs the `solutions_draft` step for `problem_id`.

    Input: the spec's `solutions` section (`algorithm_hint`, `wanted_verdicts`,
    `languages`, `alternative_approaches`, `known_wrong_approaches`) plus the fixed asymptotics
    from `constraints.intended_complexity` — it's already validated as
    non-empty (`orchestrator.spec.Constraints`) and isn't recomputed,
    only passed to the prompt as a given fact. Plus the statement from
    `statement_draft` in its input/output part (input and output format,
    indexing, examples with answers, the legend — see `_build_context_sections`)
    and the full text of the `constraints_pick` upstream artifact
    (`upstream_artifacts["constraints.yaml"]`: variable ranges, groups,
    the validator's guarantees; without TL/ML — `base.constraints_without_limits`) — without them the model
    is forced to guess the input/output format and the types of the values.

    Requires the optional `solutions` section to be on in the spec
    (`spec.solutions is not None`) — without it the author didn't order
    solution drafts, and running the step makes no sense.

    The model must take the algorithm ONLY from `solutions.algorithm_hint` (and
    `solutions.alternative_approaches` for additional `ok` solutions) and has no
    right to invent another algorithm or change the declared
    asymptotics (CLAUDE.md, "Role of each generative step", item 4: "solution
    drafts with declared verdicts... these aren't final solutions", and
    `docs/PROMPTS.md`, rule 1 for `solutions_draft`: "don't invent another
    algorithm"). If `algorithm_hint` is empty or too vague — `status:
    uncertain`, and the call ends with `StepUncertainError` without writing files, rather
    than an attempt to make up the algorithm from the legend/statement.

    On success it writes the solution files to `problems/<problem_id>/solutions/`.
    """
    _require_solutions_section(problem_id, spec)
    constraints_yaml = constraints_without_limits(
        require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    )

    context = {
        "problem_id": spec.problem_id,
        **_build_context_sections(spec),
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        return (
            Path(problems_dir)
            / problem_id
            / SOLUTIONS_SUBDIR
            / artifact_filename(filename, SOLUTIONS_SUBDIR)
        )

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        for filename in artifacts:
            resolve_artifact_path(filename)

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        # The files are independent: generators/the script and solutions are applied one by one.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
