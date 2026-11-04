"""The shared loop of one generative step.

Each of the steps (`statement_draft`, `constraints_pick`, `tests_plan`,
`generators_draft`, `test_script_draft`, `solutions_draft`, `checker_draft`,
`interactor_draft`, see CLAUDE.md,
"Role of each generative step") is the same sequence of
actions over different data:

1. render `prompts/<step>/user.md.j2` (Jinja2) from the spec section
   (+ upstream artifacts, if the step depends on them);
2. read `prompts/<step>/system.md` as the system prompt;
3. call `model_router.call_model(...)`;
4. handle `ModelResponse.status` by the "Escalation rule on uncertainty"
   (CLAUDE.md): `uncertain` — stop the step and don't write artifacts;
   `confirmed`/`proposed` — write the artifacts to `problems/<problem_id>/...`,
   run `compile_check` on all `.cpp` artifacts and save the
   `CacheEntry` via `cache.py`.

Revision mode (`StepRevision`): if the step's files already exist on disk, the model
gets them together with what changed in the step's input since the last run
(`prompts/revise.md.j2`), and revises them rather than writing from scratch. Each
current file in the response is in exactly one of: `artifacts` (changed),
`unchanged`, `removed`, `renamed[].from` (`_plan_response`). Removed
files aren't deleted but renamed to `<name>.removed`
(`REMOVED_SUFFIX`) — the author looks and deletes them themselves; renames
are applied immediately. Previous versions of overwritten and renamed files
(in any mode) — in the problem directory's `.llm_backup/<time>/`
(`problems/` is outside git, the problem's files have no history).

The response is applied like a `git merge`: what can't be applied (an invalid
file name, a current file in several places of the response at once, a rename to
an occupied name) is a conflict: the problem file stays as it was, the model's version
is set aside in `.llm_conflicts/<step>-<time>/` with a `CONFLICTS.md` report, and
the rest is applied. Steps whose files are tied to each other
(`partial_apply=False`), and a response that failed `validate_artifacts`,
are applied only in full: on a conflict nothing is applied. A cache entry
with conflicts isn't a cache hit until the author resolves them and runs
`llm accept`.

An incomplete response — missing some of the files the step must return
(`MissingArtifactsError` from `validate_artifacts`) — the model completes once
with a separate call (`MISSING_ARTIFACTS_PROMPT`): it is shown
the files already returned, and it returns only the missing ones.

This module implements steps 1-5 once (`run_generative_step`); the concrete
modules in `orchestrator/steps/*.py` only know which spec section to read,
how to lay out the artifacts into the `problems/<id>/...` directories and what goes into the
upstream hash — they don't duplicate this loop themselves.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

import jinja2
import yaml

from orchestrator import model_router
from orchestrator.cache import (
    REVISE_PROMPT,
    CacheEntry,
    PendingResponse,
    changed_input_values,
    compute_prompt_hash,
    compute_prompt_parts,
    discard_pending_response,
    input_parts_from_values,
    save_cache_entry,
    save_pending_response,
)
from orchestrator.checks.compile_check import CompileResult, compile_check
from orchestrator.checks.source_charset import charset_error
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    PROBLEMS_DIR,
    REMOVED_SUFFIX,
    is_authoring_name,
    is_removed_mark,
)
from orchestrator.statement_languages import DEFAULT_LANGUAGE

PROMPTS_DIR = Path("prompts")
TEMPLATES_DIR = Path("templates")

# `REVISE_PROMPT` (imported from `cache.py`) — the revision-mode user-prompt block
# shared by all steps (relative to prompts_dir): appended to
# `prompts/<step>/user.md.j2` when the model is shown the step's current files.

# The re-request-of-missing-files user-prompt block shared by all steps
# (relative to prompts_dir): appended to the user prompt of the first call
# if its response lacked some of the mandatory files. It isn't part of the step's
# prompt hash: it doesn't set the step's result — it only asks to add what the
# main prompt required anyway.
MISSING_ARTIFACTS_PROMPT = "missing_artifacts.md.j2"

# The directory (in the problem directory) with the previous versions of files the step
# overwrote or renamed: `.llm_backup/<time>/<path in the problem directory>`.
LLM_BACKUP_DIRNAME = ".llm_backup"
# The directory (in the problem directory) with what of the model's response wasn't applied:
# `.llm_conflicts/<step>-<time>/` — the model's versions of files and the
# `CONFLICTS_REPORT` report; files with invalid names — in `INVALID_NAMES_SUBDIR`.
LLM_CONFLICTS_DIRNAME = ".llm_conflicts"
CONFLICTS_REPORT = "CONFLICTS.md"
# The conflict reason for the parts of a response not applied in full: the reason itself
# is one for the whole response (`StepResult.not_applied_reason`), it isn't repeated for the files.
WHOLE_RESPONSE_NOT_APPLIED = "the response is not applied as a whole"
INVALID_NAMES_SUBDIR = "invalid_names"
# The sample problems `examples/` (`EXAMPLES_DIR`), each in the
# `problems/<problem_id>/` layout plus its own spec `spec.yaml` (how problems are laid out
# in nested directories — see `orchestrator/problem_paths.py`). They aren't attached to the
# call by themselves — only as spec references (`orchestrator/references.py`).

# Each problem has its own copy of `problem_lib.h` — `problems/<problem_id>/problem_lib.h`:
# the template `templates/problem_lib.h` is only its initial content, and the author's
# edits for a particular problem are overwritten neither by the template, nor by
# `llm generate`, nor by `polygon push`. The copy is created by `ensure_problem_lib` —
# before the compile-check of the generated `.cpp` and in the polygon step
# `upload_problem_lib`; `polygon pull` downloads the resource file from
# Polygon into it. The model is given the template (`STEP_CONTEXT_DOCUMENTS`).
PROBLEM_LIB_FILENAME = "problem_lib.h"

# The rules for interactive problems (`spec.interactive`): `interactor_draft`
# always gets them, `statement_draft` and `solutions_draft` — only for an
# interactive problem (via `extra_context_documents`), so that editing them doesn't
# invalidate the cache of these steps for normal problems.
INTERACTIVE_TUTORIAL = "tutorials/interactive.md"

# The mandatory context documents per step (paths relative to templates_dir),
# see CLAUDE.md, "How to use the attached materials": tutorials/*.md
# are rules, not reference material. The rules are split by topic, and a step
# gets only the topics it needs: editing one topic invalidates the cache only of
# the steps that read it (tutorials/polygon.md — a reference on the Polygon interface,
# not attached to any step). `constraints_format.yaml` — the format of the
# `constraints_pick` result, which the following steps and the
# orchestrator read (mandatory even with a spec without references), and problem_lib.h —
# the base to extend. The prompts (prompts/<step>/system.md) only refer to these
# files by name — here their contents are actually attached to the model
# call (see `call_model` in `model_router.py` and `LLMClient.generate`), not
# retold.
#
# An entry with `*` is a glob pattern relative to templates_dir: all
# matching files are attached (in sorted order). Samples (files of other problems)
# aren't listed here — they depend on the references of a particular spec, see
# the steps' `extra_context_documents` and `orchestrator/references.py`.
STEP_CONTEXT_DOCUMENTS: dict[str, list[str]] = {
    "statement_draft": [
        "tutorials/statement.md",
    ],
    "constraints_pick": [
        "tutorials/limits.md",
        "tutorials/validator.md",
        "constraints_format.yaml",
        "problem_lib.h",
    ],
    "tests_plan": [
        "tutorials/tests.md",
        "tests_plan_format.yaml",
    ],
    "generators_draft": [
        "tutorials/tests.md",
        "problem_lib.h",
    ],
    "test_script_draft": [
        "tutorials/tests.md",
        "tutorials/freemarker.md",
        "test_script_functions.ftl",
    ],
    "solutions_draft": [
        "tutorials/solutions.md",
    ],
    "checker_draft": [
        "tutorials/checker.md",
        "problem_lib.h",
    ],
    "interactor_draft": [
        INTERACTIVE_TUTORIAL,
        "problem_lib.h",
    ],
    "tutorial_draft": [
        "tutorials/tutorial.md",
    ],
}

# The statuses of the model's response by the contract from docs/PROMPTS.md.
STATUS_CONFIRMED = "confirmed"
STATUS_PROPOSED = "proposed"
STATUS_UNCERTAIN = "uncertain"
_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}


# The result of `constraints_pick`, which the following steps read (via
# `upstream_artifacts`, CLAUDE.md, "Caching by spec hash").
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# TL/ML in `constraints.yaml`: the `limits` section and the pair of keys that
# `set_constraints` also accepts right at the document root, see
# `constraints_without_limits`.
_LIMITS_BLOCK_RE = re.compile(r"limits\s*:")
_LIMITS_KEY_RE = re.compile(r"(time_limit_seconds|memory_limit_mb)\s*:")


def require_constraints_yaml(
    step_name: str, problem_id: str, upstream_artifacts: dict[str, Any] | None
) -> str:
    """`constraints.yaml` from the `upstream_artifacts` of a step that
    depends on it; missing — `ValueError` (the steps were run out of order). `step_name` —
    so that the message names the step that triggered the check."""
    if not upstream_artifacts or CONSTRAINTS_ARTIFACT not in upstream_artifacts:
        raise ValueError(
            f"Step '{step_name}' for '{problem_id}': upstream_artifacts has no "
            f"'{CONSTRAINTS_ARTIFACT}' — the 'constraints_pick' step must "
            "succeed first"
        )
    return upstream_artifacts[CONSTRAINTS_ARTIFACT]


def constraints_without_limits(constraints_yaml: str) -> str:
    """`constraints.yaml` without TL/ML: the top-level `limits` sections (together
    with the nested lines) and the `time_limit_seconds`/`memory_limit_mb` keys
    at the root. Tests and solutions are built from the asymptotics and the variable
    ranges: the model can't check the TL, and tuning it (including on
    Polygon) mustn't invalidate the steps. It's cut out as text, not through
    YAML, so that the model sees the rest of the file, with comments, as is.
    """
    kept: list[str] = []
    in_limits = False
    for line in constraints_yaml.splitlines(keepends=True):
        if in_limits and (line[:1] in (" ", "\t") or not line.strip()):
            continue
        in_limits = bool(_LIMITS_BLOCK_RE.match(line))
        if in_limits or _LIMITS_KEY_RE.match(line):
            continue
        kept.append(line)
    return "".join(kept)


class StepUncertainError(Exception):
    """The step is stopped because of `status: uncertain` in the model's response.

    Implements the "Escalation rule on uncertainty" (CLAUDE.md): if the
    response lacks data explicitly set by the author in the spec, the model must
    return `uncertain` instead of making up a value, and the orchestrator
    must stop the pipeline for this `problem_id` and not touch
    `problems/` — this exception class is that mechanical stop signal.
    """

    def __init__(self, step_name: str, problem_id: str, notes: list[dict[str, Any]]):
        self.step_name = step_name
        self.problem_id = problem_id
        self.notes = notes
        details = (
            "; ".join(f"{note.get('field', '?')}: {note.get('explanation', '')}" for note in notes)
            or "the model did not provide notes with the missing fields"
        )
        super().__init__(
            f"Step '{step_name}' for '{problem_id}' stopped (status: uncertain): "
            f"{details}. Fix problems/{problem_id}/spec.yaml and run the step again."
        )


class MissingArtifactsError(ValueError):
    """The model's response is incomplete: it lacks some of the files the step must
    return (`missing` — their names, as the model would return them). Raised from
    the step's `validate_artifacts` (`check_artifact_names`); `run_generative_step`
    requests these files from the model once more."""

    def __init__(self, message: str, missing: list[str]):
        super().__init__(message)
        self.missing = missing


def check_artifact_names(step_name: str, expected: set[str], artifacts: dict[str, str]) -> None:
    """Checks the set of the step's file names: exactly `expected`. A shortage is a
    `MissingArtifactsError` (it can be requested again), only extra files — a
    `ValueError`."""
    actual = set(artifacts)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        message = f"Step '{step_name}': incomplete response — missing files: {missing}"
        if extra:
            message += f"; unexpected files: {extra}"
        raise MissingArtifactsError(message, missing)
    if extra:
        raise ValueError(f"Step '{step_name}': unexpected files in the response: {extra}")


@dataclass
class GeneratedArtifacts:
    """The step's files written by the orchestrator, not the model (e.g. the input of the
    statement's examples — a copy from the spec): the model doesn't return them, and if it
    did — its version is discarded (an `ignored` note).

    `artifacts` — name (as the model would return it) -> contents; they are part of
    the set checked by `validate_artifacts`, and are written together with the
    model's response — only if it's applied. `stale` — the same kind of files from
    previous runs that no longer exist (name -> path): they are marked
    `.removed`, like files removed by the model."""

    artifacts: dict[str, str]
    stale: dict[str, Path] = field(default_factory=dict)
    reason: str = "written by the orchestrator from the spec"


@dataclass
class StepResult:
    """The result of a successful (`confirmed`/`proposed`) run of a step.

    `notes` keeps the model's notes as is (incl. `kind: proposed`) —
    which fields to mark as "requires the author's manual review" in
    `problems/` is decided by the calling code (the pipeline), see CLAUDE.md, "Escalation
    rule on uncertainty".
    """

    step_name: str
    problem_id: str
    status: str
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    # The paths from `artifact_paths` that didn't exist before writing (new files).
    created: list[Path] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    compile_results: dict[str, CompileResult] = field(default_factory=dict)
    # Written source files with non-ASCII characters (artifact name -> message),
    # see `orchestrator/checks/source_charset.py`.
    charset_errors: dict[str, str] = field(default_factory=dict)
    # Revision mode: files marked `.removed` (mark path, reason),
    # and renames (old path, new path, reason).
    revised: bool = False
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    # Where the previous versions of overwritten/renamed files went;
    # `None` — nothing was overwritten.
    backup_dir: Path | None = None
    # What of the model's response wasn't applied (file name, reason) and the directory where
    # the model's versions were set aside (`_write_conflicts`); `None` — everything was applied.
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    conflicts_dir: Path | None = None
    # Why the response wasn't applied in full (it failed `validate_artifacts` or
    # a conflict in a step applied only in full); `None` — it was applied.
    not_applied_reason: str | None = None


@dataclass
class StepRevision:
    """Revision mode: the step has already run, and its files are on disk.

    `current_paths` — the step's current files: artifact name (as the
    model would return it, see the steps' `current_artifacts`) -> path. `previous_input_values`
    — the previous run's input by parts (`CacheEntry.input_values`), from it the
    model is shown what changed; `None` — unknown (there's no cache entry
    or it's older than `input_values`). `other_changes` — what else
    changed since the last run besides the input (the model, the prompt, the attached
    documents) — only names, without contents.
    """

    current_paths: dict[str, Path]
    previous_input_values: dict[str, Any] | None = None
    other_changes: list[str] = field(default_factory=list)


def _jinja_env(prompts_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(prompts_dir)),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def with_default_lists(data: dict[str, Any], list_fields: list[str]) -> dict[str, Any]:
    """A copy of `data` where `None` under the keys from `list_fields` is replaced with `[]`.

    In `docs/SPEC_FORMAT.md` some spec fields are `Optional[list[...]] = None`
    (e.g. `known_ambiguities`, `special_guarantees`) deliberately
    allowing `null` as "the author specified nothing". The `user.md.j2` templates
    (see `docs/PROMPTS.md`) iterate over such fields with `{% for %}` without
    a separate check for `null` — with `None` instead of `[]` rendering fails with
    `TypeError`, so the normalization is needed before calling Jinja, not in the template.
    """
    result = dict(data)
    for key in list_fields:
        if result.get(key) is None:
            result[key] = []
    return result


def language_input(spec: Any) -> dict[str, Any]:
    """The statement language (the spec's `language`) as part of the input of a step that writes
    text in it (`statement_draft`, `tutorial_draft`): `DEFAULT_LANGUAGE` has no
    key — its appearance in the spec didn't invalidate the cache of these steps for
    problems written before it."""
    return {} if spec.language == DEFAULT_LANGUAGE else {"language": spec.language}


def statement_io_section(spec: Any, *, include_output_and_legend: bool) -> dict[str, Any]:
    """The problem's input/output format from the spec's `statement_draft` section — for
    steps that don't depend on this section by themselves, but without it
    are forced to guess the format (validator, generators, solutions).

    It's exactly the spec that is taken, not the generated `problems/<id>/statement/*.tex`:
    the spec is the primary source, and it exists even if `statement_draft` went to
    `uncertain`. The returned dict is both the context for
    `user.md.j2` and part of the step's cache hash input (a single assembly point, so that they
    don't diverge).

    `include_output_and_legend=False` — only the input (`formal_input_sketch`,
    `indexing`, the examples' input): for `constraints_pick` the legend isn't
    passed on purpose — deriving the asymptotics from it is forbidden, and an extra
    reason to do so in the prompt isn't needed. `True` — additionally
    `formal_output_sketch`, `legend_sketch` and the examples' output (`null` if
    the author didn't give it) — for solutions, which need both to read the input and
    print the answer — and for an interactive problem the interaction protocol
    (`interaction_sketch`; a normal one has no key, so as not to change the hash).
    """
    statement = spec.statement_draft
    section: dict[str, Any] = {
        "formal_input_sketch": statement.formal_input_sketch,
        "indexing": statement.indexing,
    }
    if include_output_and_legend:
        section["legend_sketch"] = statement.legend_sketch
        section["formal_output_sketch"] = statement.formal_output_sketch
        section["sample_examples"] = [
            {"input": ex.input, "output": ex.output} for ex in statement.sample_examples
        ]
        if statement.has_interaction:
            section["interaction_sketch"] = statement.interaction_sketch
    else:
        section["sample_examples"] = [{"input": ex.input} for ex in statement.sample_examples]
    return section


def render_user_prompt(
    step_name: str, context: dict[str, Any], *, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """Renders `prompts/<step_name>/user.md.j2` from `context`."""
    env = _jinja_env(prompts_dir)
    template = env.get_template(f"{step_name}/user.md.j2")
    return template.render(**context)


def _display_value(value: Any) -> str | None:
    """A part of the step's input as text for the prompt: a string — as is (the
    legend text, the contents of `constraints.yaml`), lists and dicts — YAML,
    other values — JSON (`true`, `5`, `null` for a field set to null)."""
    if isinstance(value, str):
        return value
    if not isinstance(value, (dict, list)):
        return json.dumps(value)
    return yaml.safe_dump(
        value, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")


def render_revision_prompt(
    revision: StepRevision,
    input_values: dict[str, Any],
    *,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    """The revision-mode block (`prompts/revise.md.j2`): the step's current files and
    what changed in the step's input since the last run (`None` for "before" —
    the part didn't exist, for "after" — it no longer exists; a field with a null value —
    `null`)."""
    previous = revision.previous_input_values
    changes = changed_input_values(previous, input_values)
    context = {
        "current_files": [
            {"name": name, "content": path.read_text(encoding="utf-8").rstrip("\n")}
            for name, path in revision.current_paths.items()
        ],
        "changes": None
        if changes is None
        else [
            {
                "name": name,
                "old": _display_value(old) if name in previous else None,
                "new": _display_value(new) if name in input_values else None,
            }
            for name, old, new in changes
        ],
        "other_changes": revision.other_changes,
    }
    return _jinja_env(prompts_dir).get_template(REVISE_PROMPT).render(**context)


def render_missing_artifacts_prompt(
    missing: list[str], returned: dict[str, str], *, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """The re-request block (`MISSING_ARTIFACTS_PROMPT`): which files weren't in the
    response and what the model has already returned."""
    context = {
        "missing": missing,
        "returned_files": [
            {"name": name, "content": content.rstrip("\n")} for name, content in returned.items()
        ],
    }
    return _jinja_env(prompts_dir).get_template(MISSING_ARTIFACTS_PROMPT).render(**context)


def read_system_prompt(step_name: str, *, prompts_dir: Path = PROMPTS_DIR) -> str:
    """Reads `prompts/<step_name>/system.md` as the system prompt."""
    return (Path(prompts_dir) / step_name / "system.md").read_text(encoding="utf-8")


def _expand_context_entry(step_name: str, entry: str, templates_dir: Path) -> list[str]:
    """Expands a `STEP_CONTEXT_DOCUMENTS` entry into a list of paths relative to
    `templates_dir`: an ordinary path — as is, a glob entry (with `*`) — into all
    matching files in sorted order (the order is part of
    `compute_prompt_hash`, so it must be deterministic).
    """
    if "*" not in entry:
        if not (templates_dir / entry).exists():
            raise FileNotFoundError(
                f"Step '{step_name}': context document '{entry}' from "
                f"STEP_CONTEXT_DOCUMENTS not found at {templates_dir / entry}"
            )
        return [entry]

    matches = sorted(
        path.relative_to(templates_dir).as_posix()
        for path in templates_dir.glob(entry)
        if path.is_file()
    )
    if not matches:
        raise FileNotFoundError(
            f"Step '{step_name}': context document pattern '{entry}' from "
            f"STEP_CONTEXT_DOCUMENTS matches no files in {templates_dir}"
        )
    return matches


def _context_document_key(path: Path, templates_dir: Path) -> str:
    """The name under which a document from `extra_paths` is attached to the call.

    A file inside `templates_dir` gets the same kind of key as the documents of the
    fixed list — the path relative to `templates_dir`
    (`problem_lib.h`). This way it isn't attached a second
    time if two references lead to it (a shared directory and a per-step file from
    it), and the key (it's part of `compute_prompt_hash`) doesn't depend on how
    `templates_dir` itself is written — relative or absolute. A file outside
    `templates_dir` stays under the path as given
    (`examples/<problem_id>/validator.cpp`, `problems/<problem_id>/validator.cpp`),
    except the `authoring/` files (the spec, `constraints.yaml`): they go under a path without
    `authoring/` (`examples/<problem_id>/constraints.yaml`), under the same
    name under which the step returns its own `constraints.yaml` — and the key
    (and with it the prompt hash) is the same as before these files moved into `authoring/`.
    """
    try:
        return path.resolve().relative_to(templates_dir.resolve()).as_posix()
    except ValueError:
        if path.parent.name == AUTHORING_DIRNAME and is_authoring_name(path.name):
            return str(path.parent.parent / path.name)
        return str(path)


def load_context_documents(
    step_name: str,
    *,
    templates_dir: Path = TEMPLATES_DIR,
    extra_paths: list[str] | None = None,
) -> dict[str, str]:
    """Reads the context documents for the step `step_name`.

    Order: first the fixed list `STEP_CONTEXT_DOCUMENTS[step_name]`
    (paths relative to `templates_dir`; glob entries are expanded into a
    sorted list of files), then `extra_paths` — paths
    relative to the repository root, specific to a particular call of the step
    (the spec's reference files, see `orchestrator/references.py`), not to
    all calls of the step in general.

    A missing file (or a glob entry without a single match) is a clear
    error BEFORE calling the model, stating the path and where it came from
    (the fixed list or `extra_paths`) — we don't fail silently and don't
    skip the document.
    """
    documents: dict[str, str] = {}

    for entry in STEP_CONTEXT_DOCUMENTS.get(step_name, []):
        for rel_path in _expand_context_entry(step_name, entry, Path(templates_dir)):
            documents[rel_path] = (Path(templates_dir) / rel_path).read_text(encoding="utf-8")

    for rel_path in extra_paths or []:
        path = Path(rel_path)
        key = _context_document_key(path, Path(templates_dir))
        if key in documents:
            continue
        if not path.exists():
            raise FileNotFoundError(
                f"Step '{step_name}': extra context document "
                f"'{rel_path}' (extra_context_documents) not found at {path}"
            )
        documents[key] = path.read_text(encoding="utf-8")

    return documents


def template_problem_lib_path(templates_dir: Path = TEMPLATES_DIR) -> Path:
    return Path(templates_dir) / PROBLEM_LIB_FILENAME


def problem_lib_path(problem_id: str, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return Path(problems_dir) / problem_id / PROBLEM_LIB_FILENAME


def ensure_problem_lib(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR, templates_dir: Path = TEMPLATES_DIR
) -> Path:
    """The path to the problem's `problem_lib.h`; if it doesn't exist — first copies the
    template there. No template either — `FileNotFoundError`."""
    path = problem_lib_path(problem_id, problems_dir)
    if not path.exists():
        template = template_problem_lib_path(templates_dir)
        if not template.exists():
            raise FileNotFoundError(f"template {template} not found — check templates_dir")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
    return path


def artifact_filename(filename: str, subdir: str | None = None) -> str:
    """The artifact's file name without a directory, for steps that decide themselves into
    which directory of `problems/<problem_id>/` to put it. The headers of the samples
    the model sees are paths like `.../generators/gen_rand.cpp`, and the
    model may return the key `generators/gen_rand.cpp` instead of
    `gen_rand.cpp`: the `subdir` prefix (the directory the step puts the file into anyway)
    is stripped. Any other path is a `ValueError`: otherwise the file would go into a
    nested directory (`generators/generators/`) or outside the problem."""
    parts = PurePosixPath(filename).parts
    if subdir is not None and len(parts) == 2 and parts[0] == subdir:
        parts = parts[1:]
    if len(parts) != 1 or parts[0] in (".", ".."):
        expected = f"'<name>' or '{subdir}/<name>'" if subdir else "'<name>'"
        raise ValueError(
            f"artifact name '{filename}' from the model is not a plain file name "
            f"(expected {expected})"
        )
    return parts[0]


def existing_artifacts(candidates: dict[str, Path]) -> dict[str, Path]:
    """Those of `candidates` (artifact name -> path) that exist on disk and aren't
    marked as removed — for the steps' `current_artifacts`."""
    return {
        name: path
        for name, path in candidates.items()
        if path.is_file() and not is_removed_mark(path)
    }


def directory_artifacts(directory: Path, pattern: str = "*") -> dict[str, Path]:
    """The files of `directory` by `pattern` under their own names (alphabetically), except
    those marked as removed."""
    if not directory.is_dir():
        return {}
    return existing_artifacts(
        {path.name: path for path in sorted(directory.glob(pattern), key=lambda p: p.name)}
    )


@dataclass
class _Conflict:
    """A part of the model's response that wasn't applied: the model's version of the file
    (`content`, if any) is set aside in the conflicts directory
    (`LLM_CONFLICTS_DIRNAME`), the problem file stays as it was."""

    filename: str
    reason: str
    content: str | None = None


@dataclass
class _ApplyPlan:
    """What to do with the step's files according to the model's response.

    `artifacts` — new and changed files to write; `final_artifacts` —
    the step's final set of files (checked by `validate_artifacts`):
    written + left as is + renamed, without removed ones;
    `conflicts` — what of the response wasn't applied; `notes` — what was applied not
    literally (e.g. a current file the response is silent about was left as
    is)."""

    artifacts: dict[str, str]
    final_artifacts: dict[str, str]
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    conflicts: list[_Conflict] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)


def _note(field_name: str, kind: str, explanation: str) -> dict[str, Any]:
    return {"field": field_name, "kind": kind, "explanation": explanation}


def _plan_response(
    response: Any,
    revision: StepRevision | None,
    resolve_path: Callable[[str], Path],
) -> _ApplyPlan:
    """Splits the model's response into what can be applied and conflicts —
    like `git merge`: what's applicable is applied, the rest is set aside for the author.

    A file with an invalid name (`resolve_path` raises `ValueError`) is a
    conflict. In revision mode each current file of the step must be in exactly
    one of `artifacts`/`unchanged`/`removed`/`renamed[].from` (files
    are matched by path, not by the name string: `generators/gen.cpp` and
    `gen.cpp` are one file):
    - in none — it's left as is (a `kept` note);
    - in several — a conflict: the current file isn't touched, the model's version
      is set aside;
    - an unknown name in `unchanged`/`removed`/`renamed[].from` —
      ignored (an `ignored` note);
    - a rename to an occupied or duplicate name — a conflict: the file isn't
      renamed, its new version (if any) is set aside."""
    plan = _ApplyPlan(artifacts={}, final_artifacts={})
    for filename, content in response.artifacts.items():
        try:
            resolve_path(filename)
        except ValueError as exc:
            plan.conflicts.append(_Conflict(filename, str(exc), content))
        else:
            plan.artifacts[filename] = content

    if revision is None:
        if response.unchanged or response.removed or response.renamed:
            plan.notes.append(
                _note(
                    "response",
                    "ignored",
                    "the model returned unchanged/removed/renamed although it wasn't shown "
                    "the current files (generation from scratch) — ignored",
                )
            )
        plan.final_artifacts = dict(plan.artifacts)
        return plan

    current_paths = revision.current_paths
    current_by_path = {path.resolve(): name for name, path in current_paths.items()}

    def current_name(filename: str) -> str | None:
        try:
            return current_by_path.get(resolve_path(filename).resolve())
        except ValueError:
            return None

    def ignored(where: str, filename: str) -> None:
        plan.notes.append(
            _note(filename, "ignored", f"{where}: not a current file of the step — ignored")
        )

    places: dict[str, list[str]] = {name: [] for name in current_paths}
    modified: dict[str, str] = {}  # current name -> the name in the response's artifacts
    for filename in plan.artifacts:
        name = current_name(filename)
        if name is not None:
            places[name].append("artifacts")
            modified[name] = filename
    unchanged: list[str] = []
    for filename in response.unchanged:
        name = current_name(filename)
        if name is None:
            ignored("unchanged", filename)
        else:
            places[name].append("unchanged")
            unchanged.append(name)
    removed: list[tuple[str, str]] = []
    for entry in response.removed:
        name = current_name(entry["filename"])
        if name is None:
            ignored("removed", entry["filename"])
        else:
            places[name].append("removed")
            removed.append((name, entry["reason"]))
    renamed: list[tuple[str, dict[str, str]]] = []
    for entry in response.renamed:
        name = current_name(entry["from"])
        if name is None:
            ignored("renamed.from", entry["from"])
        else:
            places[name].append("renamed.from")
            renamed.append((name, entry))

    contested = {name for name, where in places.items() if len(where) > 1}
    for name in sorted(contested):
        filename = modified.pop(name, name)
        plan.conflicts.append(
            _Conflict(
                filename,
                f"in several places of the response ({', '.join(places[name])}) "
                "— the current file is kept as is",
                plan.artifacts.pop(filename, None),
            )
        )
    for name, where in places.items():
        if not where:
            plan.notes.append(_note(name, "kept", "not mentioned in the response — kept as is"))

    gone: set[str] = set(modified)
    for name, reason in removed:
        if name not in contested:
            plan.removed.append((current_paths[name], reason))
            gone.add(name)
    targets: set[Path] = set()
    renamed_contents: dict[str, str] = {}
    for name, entry in renamed:
        to = entry["to"]
        if name in contested:
            problem = f"new name of '{name}', which is in several places of the response"
        else:
            try:
                target = resolve_path(to)
            except ValueError as exc:
                problem = str(exc)
            else:
                if target.resolve() in current_by_path or target.exists():
                    problem = f"'{name}' -> '{to}': the new name is already taken"
                elif target.resolve() in targets:
                    problem = f"'{name}' -> '{to}': the new name is used twice"
                else:
                    problem = None
        if problem is not None:
            if name not in contested:
                problem += f" — '{name}' is not renamed"
            plan.conflicts.append(_Conflict(to, problem, plan.artifacts.pop(to, None)))
            continue
        targets.add(target.resolve())
        plan.renamed.append((current_paths[name], target, entry["reason"]))
        renamed_contents[to] = current_paths[name].read_text(encoding="utf-8")
        gone.add(name)

    plan.final_artifacts = {
        name: path.read_text(encoding="utf-8")
        for name, path in current_paths.items()
        if name not in gone
    }
    plan.final_artifacts.update(renamed_contents)
    plan.final_artifacts.update(plan.artifacts)
    return plan


def _add_generated_artifacts(
    plan: _ApplyPlan, generated: GeneratedArtifacts, resolve_path: Callable[[str], Path]
) -> set[str]:
    """The orchestrator's files — into the step's final set of files; the model's versions of them
    are discarded (their names in the response are returned)."""
    dropped: set[str] = set()
    generated_paths = {resolve_path(name).resolve() for name in generated.artifacts}
    for filename in list(plan.artifacts):
        if resolve_path(filename).resolve() in generated_paths:
            del plan.artifacts[filename]
            plan.final_artifacts.pop(filename, None)
            dropped.add(filename)
            plan.notes.append(
                _note(filename, "ignored", f"returned by the model — ignored: {generated.reason}")
            )
    plan.final_artifacts.update(generated.artifacts)
    return dropped


def _plan_and_check(
    response: Any,
    revision: StepRevision | None,
    resolve_path: Callable[[str], Path],
    generated: GeneratedArtifacts | None,
    validate_artifacts: Callable[[dict[str, str]], None] | None,
    partial_apply: bool,
) -> tuple[_ApplyPlan, str | None, list[str]]:
    """`_plan_response` + checking the step's final set of files. Returns
    the plan, the reason why the response isn't applied in full (`None` —
    it's applied), and the missing files (`MissingArtifactsError`)."""
    plan = _plan_response(response, revision, resolve_path)
    dropped: set[str] = set()
    if generated is not None:
        dropped = _add_generated_artifacts(plan, generated, resolve_path)
    reason: str | None = None
    missing: list[str] = []
    if plan.conflicts and not partial_apply:
        reason = "the files of this step are applied only all together"
    elif validate_artifacts is not None:
        try:
            validate_artifacts(plan.final_artifacts)
        except MissingArtifactsError as exc:
            reason, missing = str(exc), exc.missing
        except ValueError as exc:
            reason = str(exc)
    if reason is not None:
        plan = _whole_step_conflict(response, plan, ignored=dropped)
    return plan, reason, missing


def _merge_missing_artifacts(
    response: ModelResponse,
    retry: ModelResponse,
    missing: list[str],
    resolve_path: Callable[[str], Path],
) -> ModelResponse:
    """The response with the files from the re-request: only the missing
    files are taken from it (the rest — as an `ignored` note), the notes of both responses
    are combined, the status is the worse of the two."""

    def resolved(filename: str) -> Path | None:
        try:
            return resolve_path(filename).resolve()
        except ValueError:
            return None

    wanted = {resolved(name) for name in missing} - {None}
    added = {
        filename: content
        for filename, content in retry.artifacts.items()
        if resolved(filename) in wanted
    }
    notes = [
        *response.notes,
        *retry.notes,
        _note(
            "response",
            "retried",
            f"the response lacked {missing} — asked for them in a separate call; "
            f"returned: {sorted(added) or 'none of them'}",
        ),
    ]
    ignored = sorted(set(retry.artifacts) - set(added))
    if ignored:
        notes.append(
            _note(
                "response",
                "ignored",
                f"files from the follow-up call that were not asked for: {ignored}",
            )
        )
    statuses = {response.status, retry.status}
    if retry.status not in _VALID_STATUSES or STATUS_UNCERTAIN in statuses:
        status = retry.status
    elif STATUS_PROPOSED in statuses:
        status = STATUS_PROPOSED
    else:
        status = STATUS_CONFIRMED
    return replace(response, status=status, artifacts={**response.artifacts, **added}, notes=notes)


def _whole_step_conflict(
    response: Any, plan: _ApplyPlan, *, ignored: set[str] = frozenset()
) -> _ApplyPlan:
    """The response isn't applied in full: all of the model's files and its proposals
    to remove/rename go to conflicts, the step's files aren't touched. The reason is
    one for the whole response (`StepResult.not_applied_reason`, the start of
    `CONFLICTS.md`), it isn't repeated for the files. `ignored` — the response's files
    that are discarded on application anyway (`GeneratedArtifacts`)."""
    conflicts = list(plan.conflicts)
    set_aside = {conflict.filename for conflict in conflicts if conflict.content is not None}
    for filename, content in response.artifacts.items():
        if filename not in set_aside | ignored:
            conflicts.append(_Conflict(filename, WHOLE_RESPONSE_NOT_APPLIED, content))
    for entry in response.removed:
        conflicts.append(
            _Conflict(
                entry["filename"],
                f"proposed removal ({entry['reason']}): {WHOLE_RESPONSE_NOT_APPLIED}",
            )
        )
    for entry in response.renamed:
        conflicts.append(
            _Conflict(
                entry["from"],
                f"proposed rename to '{entry['to']}' ({entry['reason']}): {WHOLE_RESPONSE_NOT_APPLIED}",
            )
        )
    return _ApplyPlan(artifacts={}, final_artifacts={}, conflicts=conflicts, notes=plan.notes)


class Backup:
    """Previous versions of the problem's files — in `.llm_backup/<time>/` (created on
    the first file)."""

    def __init__(self, problem_dir: Path) -> None:
        self.problem_dir = problem_dir
        self.directory: Path | None = None

    def save(self, path: Path) -> None:
        if self.directory is None:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.directory = self.problem_dir / LLM_BACKUP_DIRNAME / stamp
        target = self.directory / path.relative_to(self.problem_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _write_artifacts(
    artifacts: dict[str, str],
    resolve_path: Callable[[str], Path],
    backup: Backup,
    *,
    skip_backup: set[Path] = frozenset(),
    created: list[Path] | None = None,
) -> dict[str, Path]:
    """Writes the response's files; previous versions of the changed ones — to `backup`. Into
    `created` (if given) — the paths of files that didn't exist before writing."""
    written: dict[str, Path] = {}
    for filename, content in artifacts.items():
        path = resolve_path(filename)
        if created is not None and not path.exists():
            created.append(path)
        if (
            path.is_file()
            and path not in skip_backup
            and path.read_text(encoding="utf-8") != content
        ):
            backup.save(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "file"


def _write_conflicts(
    step_name: str,
    conflicts: list[_Conflict],
    problem_dir: Path,
    resolve_path: Callable[[str], Path],
    whole_step_reason: str | None,
) -> Path:
    """The `.llm_conflicts/<step>-<time>/` directory: the model's versions of files (at
    their paths in the problem directory; files with invalid names — in
    `INVALID_NAMES_SUBDIR`) and `CONFLICTS_REPORT` — what wasn't applied
    and why."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = problem_dir / LLM_CONFLICTS_DIRNAME / f"{step_name}-{stamp}"
    lines = [f"# Not applied: {step_name}", ""]
    if whole_step_reason is not None:
        lines += [f"Nothing of the response is applied: {whole_step_reason}.", ""]
    for conflict in conflicts:
        line = f"- `{conflict.filename}` — {conflict.reason}"
        if conflict.content is not None:
            try:
                relative = resolve_path(conflict.filename).relative_to(problem_dir)
            except ValueError:
                relative = Path(INVALID_NAMES_SUBDIR) / _safe_filename(conflict.filename)
            target = directory / relative
            while target.exists():
                target = target.with_name(target.name + "_")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(conflict.content, encoding="utf-8")
            line += f"; model version: `{target.relative_to(directory).as_posix()}`"
        lines.append(line)
    lines += [
        "",
        "The files of the step are left as they were for everything above. Resolve "
        f"by hand, delete this directory, then run `llm accept --step {step_name}`.",
    ]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CONFLICTS_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def _apply_removals_and_renames(plan: _ApplyPlan, backup: Backup) -> list[tuple[Path, str]]:
    """Marks removed files (`<name>.removed`; a previous mark with the same
    name — to the backup) and renames files (the old version — to the backup).
    Returns `(mark path, reason)` of the removed ones."""
    marks: list[tuple[Path, str]] = []
    for path, reason in plan.removed:
        mark = path.with_name(path.name + REMOVED_SUFFIX)
        if mark.exists():
            backup.save(mark)
        path.replace(mark)
        marks.append((mark, reason))
    for old_path, new_path, _ in plan.renamed:
        backup.save(old_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.replace(new_path)
    return marks


def run_generative_step(
    *,
    step_name: str,
    problem_id: str,
    context: dict[str, Any],
    input_hash: str,
    resolve_artifact_path: Callable[[str], Path],
    input_values: dict[str, Any] | None = None,
    revision: StepRevision | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
    validate_artifacts: Callable[[dict[str, str]], None] | None = None,
    postprocess_artifacts: (
        Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]] | None
    ) = None,
    saved_response: ModelResponse | None = None,
    partial_apply: bool = False,
    generated_artifacts: GeneratedArtifacts | None = None,
) -> StepResult:
    """The shared loop of a step: rendering the prompts, calling the model, writing the result.

    `context` — a dict already ready for Jinja (the step's spec section +
    upstream artifacts, if the step needs them); the `None -> []` normalization for
    iterated fields (see `with_default_lists`) must be done by the
    calling module of the particular step, not this function — each step has
    its own set of such fields.

    `input_hash` — the already computed `cache.compute_input_hash(...)` for this
    step; it's passed here by the calling module, since only it knows
    which spec section and which upstream artifacts affect the step (see
    CLAUDE.md, "Caching by spec hash"). `input_values` — the same input by
    parts (`cache.compute_input_values`): it and its per-part hashes
    are saved in `CacheEntry`, so that `llm status` can say what exactly
    changed, and the next run in revision mode can show it to the model.

    `revision` — revision mode (see the module docstring): `prompts/revise.md.j2`
    with the step's current files and the input changes is appended to the user prompt;
    the response is split by `_plan_response`, removed
    files are marked `.removed`, renames are applied. `None` — the model
    writes the files from scratch (the step's current files that aren't in the response stay
    on disk as is).

    `resolve_artifact_path(filename)` — how to lay out the `artifacts` of the model's
    response into the `problems/<problem_id>/...` directories (each step has its own
    layout, see CLAUDE.md, "Directory structure").

    `extra_context_documents` — additional paths (relative to the repository
    root), specific to a particular call of this step rather than to
    all of its calls in general (the spec's reference files, see
    `orchestrator/references.py` and `load_context_documents`). Together with the fixed list
    `STEP_CONTEXT_DOCUMENTS[step_name]` they are read and passed to the model as
    `context_documents`, and are also part of `compute_prompt_hash` — editing
    any of these documents must invalidate the step's cache.

    `validate_artifacts(artifacts)` — an optional check of the set of
    artifacts from the model's response (e.g. that the file names are exactly those the
    step expects); in revision mode — of the step's final set of files
    (changed + `unchanged` + renamed, without removed ones). Called
    only with `confirmed`/`proposed` (with
    `uncertain` `artifacts` may be empty/partial by the contract) and
    strictly BEFORE writing to disk: if it raises `ValueError`, nothing of the response
    is applied — everything goes to conflicts (see the module docstring), and the
    saved response stays for `llm apply`. `MissingArtifactsError`
    (an incomplete response) after a model call — one re-request of the missing files
    (`MISSING_ARTIFACTS_PROMPT`), they're added to the response (a
    `retried` note), and the check is repeated; a saved response (`llm apply`)
    has no re-request.

    `generated_artifacts` — the step's files written by the orchestrator, not the
    model (`GeneratedArtifacts`).

    `partial_apply` — the step's files are independent of each other: on a conflict in
    some of the files the rest are applied. `False` — on any conflict the response
    isn't applied at all.

    `postprocess_artifacts(artifacts)` — an optional mechanical
    post-processing of the artifacts after `validate_artifacts` and before writing to disk:
    returns a new set of artifacts and notes about what was done (added to
    the `notes` of the model's response, so that the author sees that the file was fixed by a rule, not the model).
    In revision mode it gets only new and changed files.

    The model's response (except `uncertain` — there's nothing to apply) is saved
    (`cache.PendingResponse`) before checking the response and writing the files and is deleted
    once applied (at least partially): if the response isn't applied in full, the call
    isn't lost. `saved_response` — such a
    saved response: it's applied instead of calling the model (`llm apply`);
    whether its input matches the current one and the revision mode is checked by the calling
    code (`pipeline.py`).

    With `status: uncertain` raises `StepUncertainError` and writes nothing to
    `problems/`. With `confirmed`/`proposed` writes the artifacts, compiles all
    written `.cpp` files with `compile_check` (without running — see
    CLAUDE.md, "The minimal safety net instead of a judging loop"), checks the written
    sources for non-ASCII characters (`source_charset`) and saves the
    `CacheEntry` with this `input_hash`, the current prompt hash, the step's
    model from `model_router.STEP_TO_MODEL` and the conflicts directory, if there
    are any.
    """
    input_values = input_values or {}
    if saved_response is None:
        system_prompt = read_system_prompt(step_name, prompts_dir=prompts_dir)
        user_prompt = render_user_prompt(step_name, context, prompts_dir=prompts_dir)
        if revision is not None:
            user_prompt = (
                user_prompt.rstrip("\n")
                + "\n\n"
                + render_revision_prompt(revision, input_values, prompts_dir=prompts_dir)
            )
        context_documents = load_context_documents(
            step_name, templates_dir=templates_dir, extra_paths=extra_context_documents
        )
        response = model_router.call_model(
            step_name, system_prompt, user_prompt, context_documents=context_documents
        )
    else:
        response = saved_response

    prompt_kwargs = dict(
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )
    cache_entry = CacheEntry(
        input_hash=input_hash,
        prompt_hash=compute_prompt_hash(step_name, **prompt_kwargs),
        model=model_router.STEP_TO_MODEL[step_name],
        status=response.status,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        input_parts=input_parts_from_values(input_values),
        prompt_parts=compute_prompt_parts(step_name, **prompt_kwargs),
        input_values=input_values,
    )

    def check_status(response: ModelResponse) -> None:
        if response.status not in _VALID_STATUSES:
            raise ValueError(
                f"Step '{step_name}': the model returned an unknown status "
                f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
            )
        if response.status == STATUS_UNCERTAIN:
            # Nothing to apply; the previous unapplied response of the same step is outdated.
            discard_pending_response(problem_id, step_name, problems_dir=problems_dir)
            raise StepUncertainError(step_name, problem_id, response.notes)

    def plan_and_check(response: ModelResponse) -> tuple[_ApplyPlan, str | None, list[str]]:
        return _plan_and_check(
            response,
            revision,
            resolve_artifact_path,
            generated_artifacts,
            validate_artifacts,
            partial_apply,
        )

    def save_pending(response: ModelResponse) -> None:
        save_pending_response(
            problem_id,
            step_name,
            PendingResponse(
                cache_entry=cache_entry, revised=revision is not None, response=asdict(response)
            ),
            problems_dir=problems_dir,
        )

    check_status(response)
    if saved_response is None:
        save_pending(response)
    plan, whole_step_reason, missing = plan_and_check(response)
    if missing and saved_response is None:
        # An incomplete response: the missing files — with one re-request, not a new
        # call in full (`MISSING_ARTIFACTS_PROMPT`).
        retry_prompt = (
            user_prompt.rstrip("\n")
            + "\n\n"
            + render_missing_artifacts_prompt(missing, response.artifacts, prompts_dir=prompts_dir)
        )
        retry = model_router.call_model(
            step_name, system_prompt, retry_prompt, context_documents=context_documents
        )
        response = _merge_missing_artifacts(response, retry, missing, resolve_artifact_path)
        cache_entry.status = response.status
        check_status(response)
        save_pending(response)
        plan, whole_step_reason, _ = plan_and_check(response)

    notes = list(response.notes)
    notes.extend(plan.notes)

    artifacts = plan.artifacts
    if postprocess_artifacts is not None and artifacts:
        artifacts, extra_notes = postprocess_artifacts(artifacts)
        notes.extend(extra_notes)
    if generated_artifacts is not None and whole_step_reason is None:
        artifacts = {**artifacts, **generated_artifacts.artifacts}
        plan.removed.extend(
            (path, "no longer produced from the spec")
            for path in generated_artifacts.stale.values()
        )

    problem_dir = Path(problems_dir) / problem_id
    backup = Backup(problem_dir)
    marks = _apply_removals_and_renames(plan, backup)
    renamed = plan.renamed
    created: list[Path] = []
    artifact_paths = _write_artifacts(
        artifacts,
        resolve_artifact_path,
        backup,
        skip_backup={new_path for _, new_path, _ in renamed},
        created=created,
    )
    conflicts_dir: Path | None = None
    if plan.conflicts:
        conflicts_dir = _write_conflicts(
            step_name, plan.conflicts, problem_dir, resolve_artifact_path, whole_step_reason
        )
        cache_entry.conflicts = conflicts_dir.relative_to(problem_dir).as_posix()

    to_compile = dict(artifact_paths)
    for _, new_path, _ in renamed:
        to_compile.setdefault(new_path.name, new_path)
    if any(path.suffix == ".cpp" for path in to_compile.values()):
        ensure_problem_lib(problem_id, problems_dir=problems_dir, templates_dir=templates_dir)
    compile_results: dict[str, CompileResult] = {
        # The problem's `problem_lib.h` — before the template's.
        filename: compile_check(path, [Path(problems_dir) / problem_id, Path(templates_dir)])
        for filename, path in to_compile.items()
        if path.suffix == ".cpp"
    }
    charset_errors = {
        filename: error
        for filename, path in to_compile.items()
        if (error := charset_error(path)) is not None
    }

    cache_entry.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_cache_entry(problem_id, step_name, cache_entry, problems_dir=problems_dir)
    if whole_step_reason is None:
        # A response not applied in full stays: after fixing the cause
        # `llm apply` applies it without a new model call.
        discard_pending_response(problem_id, step_name, problems_dir=problems_dir)

    return StepResult(
        step_name=step_name,
        problem_id=problem_id,
        status=response.status,
        artifact_paths=artifact_paths,
        created=created,
        notes=notes,
        compile_results=compile_results,
        charset_errors=charset_errors,
        revised=revision is not None,
        removed=marks,
        renamed=renamed,
        backup_dir=backup.directory,
        conflicts=[(conflict.filename, conflict.reason) for conflict in plan.conflicts],
        conflicts_dir=conflicts_dir,
        not_applied_reason=whole_step_reason,
    )
