"""Step 1 — a LaTeX draft of the problem statement (CLAUDE.md, "Role of each
generative step", item 1).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.statement_languages import (
    EXAMPLES_DIRNAME,
    STATEMENT_DIRNAME,
    language_name,
)
from orchestrator.steps.base import (
    INTERACTIVE_TUTORIAL,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    GeneratedArtifacts,
    StepResult,
    StepRevision,
    check_artifact_names,
    existing_artifacts,
    language_input,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "statement_draft"

# The only Optional[list[...]] field of the section over which user.md.j2
# does {% for %} without a null check (see base.with_default_lists).
_LIST_FIELDS = ["known_ambiguities"]

STATEMENT_SUBDIR = STATEMENT_DIRNAME
_FIXED_STATEMENT_FILES = ["legend.tex", "input_format.tex", "output_format.tex", "notes.tex"]
_EXAMPLE_NAME = re.compile(r"^example_(\d+)\.txt$")

# An interactive problem (`spec.interactive`): the interaction protocol is
# a separate statement block (Interaction in Polygon), and an example in the statement is
# a dialogue of the solution with the interactor, not the test's input (the interactor reads that):
# what the solution read (`input_for_statement`) and what it printed
# (`output_for_statement`), see `upload_statement`. For a normal problem the same
# files are the input/output for statements the author sets themselves (by hand or
# on Polygon, `polygon pull` downloads them): these aren't the step's artifacts, the model doesn't
# see or write them.
INTERACTION_FILE = "interaction.tex"
_DIALOGUE_NAME = re.compile(r"^example_(\d+)\.(input|output)_for_statement\.txt$")


def dialogue_filenames(number: int) -> tuple[str, str]:
    """The file names of the `number`-th example's dialogue (without `examples/`)."""
    return (
        f"example_{number}.input_for_statement.txt",
        f"example_{number}.output_for_statement.txt",
    )


def verify_filename(number: int) -> str:
    """The file name of the "verify input/output for statements" flag of the `number`-th
    example (without `examples/`): the file exists — the flag is on. Written by the author or
    `polygon pull`, not the model."""
    return f"example_{number}.verify_for_statement.txt"


def _expected_artifact_names(spec: ProblemSpec) -> set[str]:
    """Exactly the step's set of file names: 4 fixed tex files + one
    `examples/example_<N>.txt` for each `sample_examples[N-1]`; for an
    interactive problem — also `interaction.tex` and the dialogue of each example.
    `example_<N>.txt` is written by the orchestrator (`_example_inputs`), the rest — by the
    model (`prompts/statement_draft/`).
    """
    n = len(spec.statement_draft.sample_examples)
    names = set(_FIXED_STATEMENT_FILES) | {f"examples/example_{i}.txt" for i in range(1, n + 1)}
    if spec.interactive:
        names.add(INTERACTION_FILE)
        for i in range(1, n + 1):
            names.update(f"examples/{name}" for name in dialogue_filenames(i))
    return names


def _section_data(spec: ProblemSpec) -> dict[str, Any]:
    """The `statement_draft` section for the prompt and the hash. `interaction_sketch`
    only for an interactive problem — a normal one has no field in the hash, as before it
    appeared (otherwise adding it would have invalidated the cache of all problems).
    The statement language (the spec's `language`) — likewise, only if it isn't `DEFAULT_LANGUAGE`."""
    exclude = set(SPEC_REFERENCES_FIELD)
    if not spec.interactive:
        exclude.add("interaction_sketch")
    section = with_default_lists(
        spec.statement_draft.model_dump(mode="json", exclude=exclude), _LIST_FIELDS
    )
    return {**section, **language_input(spec)}


def _example_inputs(spec: ProblemSpec) -> dict[str, str]:
    """`examples/example_<N>.txt` — the input of `sample_examples[N-1]` as is.
    It's a copy from the spec, not statement text, so the orchestrator writes it, not
    the model: the model could skip a file or change the input."""
    return {
        f"examples/example_{i}.txt": example.input
        for i, example in enumerate(spec.statement_draft.sample_examples, start=1)
    }


def _stale_example_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path
) -> dict[str, Path]:
    """`example_<N>.txt` on disk that are no longer in `sample_examples`
    (there are fewer examples now) — they'll be marked `.removed`."""
    examples_dir = Path(problems_dir) / problem_id / STATEMENT_SUBDIR / EXAMPLES_DIRNAME
    if not examples_dir.is_dir():
        return {}
    n = len(spec.statement_draft.sample_examples)
    return {
        f"examples/{path.name}": path
        for path in sorted(examples_dir.glob("example_*.txt"))
        if (match := _EXAMPLE_NAME.match(path.name)) and int(match.group(1)) > n
    }


def _validate_statement_artifacts(expected: set[str]) -> Callable[[dict[str, str]], None]:
    def _validate(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, expected, artifacts)

    return _validate


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """The step's input hash without calling the model — used by `pipeline.py` to
    decide whether the cache is valid before deciding whether to call `run_step`
    (CLAUDE.md, "Caching by spec hash"). `problem_id`/`upstream_artifacts`
    aren't used here (the step doesn't depend on them) — they are present only for the sake of
    the single signature `compute_input_hash(problem_id, spec, upstream_artifacts)`
    of all steps.
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
    return _section_data(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Like for the solution stages — a directory, not a file: `pipeline.py` considers the
    cache valid if the directory exists and isn't empty. Previously this was the path to
    the single `statement.tex` — now the step has several files in the
    statement language's directory `statement/<code>/`.
    """
    return language_dir(problem_id, spec, problems_dir=problems_dir)


def language_dir(problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR) -> Path:
    """The statement directory in the generation language (the spec's `language`): `statement/<code>/`."""
    return Path(problems_dir) / problem_id / STATEMENT_SUBDIR / spec.language


def artifact_path(
    problem_id: str, spec: ProblemSpec, filename: str, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """The path of the step's artifact by its name in the model's response: `examples/...` —
    in the shared `statement/examples/`, tex files — in the generation language's directory."""
    if filename.startswith(f"{EXAMPLES_DIRNAME}/"):
        return Path(problems_dir) / problem_id / STATEMENT_SUBDIR / filename
    return language_dir(problem_id, spec, problems_dir=problems_dir) / filename


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """The step's current files on disk: artifact name (as the
    model would return it) -> path — for revision mode (`base.StepRevision`).
    The 4 tex files, `interaction.tex` and the examples' dialogues
    `examples/example_<N>.{input,output}_for_statement.txt` (by ascending
    N) — only for an interactive problem: for a normal one these are the author's input/output for
    statements. Not the model's artifacts: `examples/example_<N>.txt` (they're
    written by the orchestrator from the spec, `_example_inputs`), the flags
    `example_<N>.verify_for_statement.txt`, `name.txt` (written by
    `polygon push`/`pull`) and the tutorial `tutorial.tex` (written by the author, `polygon
    pull` or the `tutorial_draft` step)."""
    candidates = {
        name: artifact_path(problem_id, spec, name, problems_dir=problems_dir)
        for name in [*_FIXED_STATEMENT_FILES, INTERACTION_FILE]
    }
    examples_dir = Path(problems_dir) / problem_id / STATEMENT_SUBDIR / EXAMPLES_DIRNAME
    examples = (
        examples_dir.glob("example_*.txt") if spec.interactive and examples_dir.is_dir() else []
    )
    numbered = sorted(
        (int(match.group(1)), path.name, path)
        for path in examples
        if (match := _DIALOGUE_NAME.match(path.name))
    )
    candidates.update({f"examples/{name}": path for _, name, path in numbered})
    return existing_artifacts(candidates)


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `statement_draft.references`, see
    `orchestrator/references.py`). The single signature with all steps is needed by
    `pipeline.py` to compute `compute_prompt_hash` the same way.

    An interactive problem also gets the `INTERACTIVE_TUTORIAL` rules (before the
    references): normal problems don't need them, and editing them doesn't touch the
    statement cache of normal problems.
    """
    references = reference_documents(STEP_NAME, spec) or []
    rules = [str(Path(templates_dir) / INTERACTIVE_TUTORIAL)] if spec.interactive else []
    return [*rules, *references] or None


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
    """Runs the `statement_draft` step for `problem_id`.

    Input: the spec's `statement_draft` section (the legend, the formal input/output
    drafts, the indexing, `sample_examples`, `known_ambiguities`,
    `preserve_legend_verbatim`) — nothing else from the spec is passed to this step.
    `context` is built from the `model_dump()` of the whole section, so
    new fields of the section (like `preserve_legend_verbatim`) automatically
    get into both the prompt and `input_hash` — they don't need to be passed here
    by hand.

    The step is forbidden from making up the solution's asymptotics from the problem's legend — it's
    the exclusive area of the `constraints_pick` step (CLAUDE.md, "Role of each
    generative step", item 1: "the model **has no right** to invent the
    asymptotics from the legend on its own"). If the spec lacks data for the formal
    description of the input/output, the indexing or the precision of a real-valued answer,
    the prompt must return `status: uncertain`, and this call ends with
    `StepUncertainError` without writing a single file (see "Escalation rule
    on uncertainty").

    On success it writes into `problems/<problem_id>/statement/<language code>/` 4 fixed
    files (`legend.tex`, `input_format.tex`, `output_format.tex`,
    `notes.tex`) and one `examples/example_<N>.txt` for each element of
    `sample_examples` (only the example's input — a copy from the spec, written by the
    orchestrator, not the model); for an interactive problem
    (`spec.interactive`) — also `interaction.tex` (the interaction protocol)
    and the dialogue of each example (`example_<N>.input_for_statement.txt`,
    `example_<N>.output_for_statement.txt`). The set of file names from the model's
    response is checked BEFORE writing to disk (`_validate_statement_artifacts`):
    missing files are requested from the model once more, and if they are still
    missing or there are extra ones — the response isn't applied at all (a conflict), not a single
    file is written. `example_<N>.txt` that are no longer in the spec
    are marked `.removed`.

    `upstream_artifacts` isn't used: `statement_draft` doesn't depend on the
    results of other steps (see CLAUDE.md, "Caching by spec hash" —
    there are no upstream artifacts in this step's hash). The parameter is present
    only for the sake of the shared signature `run_step(problem_id, spec,
    upstream_artifacts)` of all steps.
    """
    context = {
        "problem_id": spec.problem_id,
        "language": language_name(spec.language),
        "statement_draft": _section_data(spec),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        # filename comes as "legend.tex" or "examples/example_1.txt" —
        # _write_artifacts creates the nested directory (parent.mkdir).
        return artifact_path(problem_id, spec, filename, problems_dir=problems_dir)

    expected = _expected_artifact_names(spec)

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        validate_artifacts=_validate_statement_artifacts(expected),
        generated_artifacts=GeneratedArtifacts(
            artifacts=_example_inputs(spec),
            stale=_stale_example_inputs(problem_id, spec, problems_dir=problems_dir),
            reason="the input of a sample is copied from the spec by the orchestrator",
        ),
    )
