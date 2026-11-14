"""The implementation of the CLI commands: `init`, `llm generate|tutorial|apply|status|accept`,
`polygon push|status|check|fix|pull`.

The argument parser and the entry point are in `cli.py`; it imports this module
only after parsing the arguments, because both pipelines are pulled in from here (and
with them pydantic and requests) — see the docstring of `cli.py`.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from orchestrator.checks.local_tests import (
    MANIFEST_FILENAME,
    TESTS_CACHE_DIRNAME,
    clear_local_caches,
    find_equal_tests,
    format_equal_tests,
    generate_tests,
    validate_tests,
)
from orchestrator.checks.solutions_run import run_solutions
from orchestrator.checks.statement_check import (
    FIX_BACKUP_DIRNAME,
    QUOTES_LANGUAGE,
    STATEMENT_DIRNAME,
    statement_files,
)
from orchestrator.checks.statement_check import (
    check_text as check_statement_text,
)
from orchestrator.checks.statement_check import (
    fix_text as fix_statement_text,
)
from orchestrator.checks.test_script_check import (
    ScriptCheckUnavailable,
    ScriptRenderError,
    check_script,
)
from orchestrator.drafts import DRAFTS_DIR, DraftError, find_draft
from orchestrator.llm_client import LLMCallError
from orchestrator.llm_report import (
    note_counts,
    write_accept_report,
    write_spec_critic_reports,
    write_spec_from_draft_report,
    write_step_report,
)
from orchestrator.pipeline import (
    OUTCOME_CACHE_HIT,
    OUTCOME_CONFIRMED,
    OUTCOME_CONFLICT,
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_NO_PENDING,
    OUTCOME_PROPOSED,
    OUTCOME_RECHECK,
    OUTCOME_SKIPPED_OPTIONAL,
    OUTCOME_UNCERTAIN,
    STATUS_ACCEPTED,
    STATUS_BLOCKED,
    STATUS_NOT_ACCEPTED,
    accept_steps,
    compute_step_statuses,
    run_pipeline,
)
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.pipeline import OUTCOME_ERROR as POLYGON_OUTCOME_ERROR
from orchestrator.polygon.pipeline import (
    compute_polygon_step_statuses,
    run_polygon_pipeline,
)
from orchestrator.polygon.pull import ITEM_CONFLICT as PULL_ITEM_CONFLICT
from orchestrator.polygon.pull import ITEM_DIFFERS as PULL_ITEM_DIFFERS
from orchestrator.polygon.pull import ITEM_DOWNLOADED as PULL_ITEM_DOWNLOADED
from orchestrator.polygon.pull import ITEM_ERROR as PULL_ITEM_ERROR
from orchestrator.polygon.pull import ITEM_KEPT as PULL_ITEM_KEPT
from orchestrator.polygon.pull import ITEM_LOCAL_ONLY as PULL_ITEM_LOCAL_ONLY
from orchestrator.polygon.pull import ITEM_OVERWRITTEN as PULL_ITEM_OVERWRITTEN
from orchestrator.polygon.pull import ITEM_UPDATED as PULL_ITEM_UPDATED
from orchestrator.polygon.pull import pull_problem
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import STATUS_DONE as POLYGON_STATUS_DONE
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_test_script import SCRIPT_FILENAMES
from orchestrator.polygon.sync import VALIDATOR_FILENAME, describe_keys
from orchestrator.problem_paths import EXAMPLES_DIR, ProblemPathError
from orchestrator.spec import (
    SpecInitError,
    prepare_spec_init,
    spec_path,
    spec_review_marks,
    spec_validation_errors,
    write_spec,
)
from orchestrator.step_order import LLM_STEPS, POLYGON_STEP_ORDER
from orchestrator.steps import tutorial_draft

_OUTCOME_LABELS = {
    OUTCOME_CACHE_HIT: "cache hit",
    OUTCOME_CONFIRMED: "confirmed",
    OUTCOME_PROPOSED: "proposed (PROPOSED, REVIEW ME)",
    OUTCOME_UNCERTAIN: "uncertain",
    OUTCOME_SKIPPED_OPTIONAL: "skipped (optional)",
    OUTCOME_NO_PENDING: "nothing to apply",
    OUTCOME_ERROR: "error",
    OUTCOME_CONFLICT: "conflict",
    OUTCOME_FAILED: "failed",
    OUTCOME_RECHECK: "rechecking",
}

# The package's logger — both the console output (see
# `_configure_console_logging`) and the per-problem log files
# `problems/<id>/llm_generation.log` / `problems/<id>/polygon.log` (see
# `_problem_log_handler`) are attached to it, see CLAUDE.md, the "execution log" task. Child
# loggers (e.g. `orchestrator.llm_clients.anthropic_client`, where
# `_log_usage` comes from) write via propagate into this same logger — they don't need
# separate setup.
LOGGER_NAME = "orchestrator"

# Separate files for generation and for Polygon: `polygon push` and `llm generate` run
# independently, and a shared file overwritten by each run would lose the log
# of one when the other is run.
LLM_GENERATION_LOG = "llm_generation.log"
POLYGON_LOG = "polygon.log"

# The run header in the problem's log file; `%s` — the whole command.
_RUN_HEADER = "===== run started: orchestrator %s ====="

# How many characters of a polygon step's error text to print to the console: the `comment`
# from Polygon can be multi-line (e.g. the validator's compilation log) —
# it's written in full to `polygon.log`. The orchestrator's own errors
# (`PolygonStepError`) aren't truncated: the hint on what to do is at their end.
_CONSOLE_ERROR_MAX_LEN = 200

_console_handler: logging.Handler | None = None


class _ListHandler(logging.Handler):
    """Accumulates the text of the records in a list — the CLI prints it itself, at its own place in the output."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _configure_console_logging(*, warnings_into: list[str] | None = None) -> logging.Logger:
    """Sets up the package's logger so that INFO messages are visible in the
    console — before this `logger.info(...)` in `_log_usage` wasn't output anywhere,
    because the logger had neither a level nor a handler.

    Recreates the handler on each call (rather than once at module import):
    `logging.StreamHandler()` captures `sys.stdout` at the moment of creation, and
    `capsys` in tests replaces `sys.stdout` for the duration of a test — a handler
    created at import would write past this replacement.

    `warnings_into` — for `polygon push`/`pull`: nothing goes to the console,
    WARNINGs accumulate in this list (the CLI prints them as a group in the summary, see
    `_print_groups`), and INFO (every HTTP request, the steps' messages) and ERROR
    (the full text of a step's error with the traceback) — only to `polygon.log`.
    """
    global _console_handler
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if _console_handler is not None:
        logger.removeHandler(_console_handler)

    if warnings_into is None:
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
    else:
        handler = _ListHandler(warnings_into)
        handler.addFilter(lambda record: record.levelno == logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _console_handler = handler
    return logger


@contextmanager
def _problem_log_handler(
    logger: logging.Logger,
    problems_dir: Path,
    problem_id: str,
    log_name: str,
    command_line: str,
) -> Iterator[logging.Handler]:
    """Sets up `problems/<problem_id>/<log_name>` for the duration of handling one
    `problem_id` — it's opened for appending: the log accumulates the history of
    runs, each run starts with a header with the command (see
    `_RUN_HEADER`) — it's convenient to search for the needed run by it, and the time on each
    line shows the duration of the steps. The handler is added to the logger
    before running the steps and removed right after, via try/finally — so that
    an unhandled exception doesn't leave the handler hanging.

    Yields the handler itself — the calling code uses it directly in
    `_log_traceback_to_file` for unhandled exceptions (see there
    why this doesn't go through the usual `logger.exception`).
    """
    problem_dir = Path(problems_dir) / problem_id
    problem_dir.mkdir(parents=True, exist_ok=True)
    log_path = problem_dir / log_name
    needs_separator = log_path.exists() and log_path.stat().st_size > 0
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    if needs_separator:
        handler.stream.write("\n")
    # Only to the file, bypassing the console handler — the command is visible in the console anyway.
    _log_to_file(handler, _RUN_HEADER % command_line)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        handler.close()


def _log_to_file(handler: logging.Handler, message: str) -> None:
    """An INFO record only to `handler` (the problem's log file), bypassing the console."""
    handler.handle(
        logging.LogRecord(
            name=LOGGER_NAME,
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=None,
            exc_info=None,
        )
    )


def _log_traceback_to_file(handler: logging.Handler, message: str) -> None:
    """Writes the current traceback (`sys.exc_info()`) to a particular `handler`,
    bypassing the logger's other handlers — so that the console output on an
    unhandled exception stays exactly the same as before (Python itself prints the traceback
    there anyway when the process exits), while
    `problems/<id>/llm_generation.log` doesn't lose the traceback (CLAUDE.md, the
    "execution log" task, item 4).
    """
    record = logging.LogRecord(
        name=LOGGER_NAME,
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=sys.exc_info(),
    )
    handler.handle(record)


def _print_pipeline_result(
    logger: logging.Logger,
    result,
    file_handler: logging.Handler,
    problem_dir: Path,
    command_line: str,
) -> None:
    """The summary of `llm generate`/`apply`: to the console — a line per step and the path to
    the report `llm_reports/<step>.md` of a step that worked (the model's notes, files,
    diffs — there, see `llm_report`); to the problem's log file — also all the notes and
    files, so that the run history in it stays complete."""
    logger.info(f"=== {result.problem_id} ===")
    if result.spec_error is not None:
        logger.info(result.spec_error)
        return

    for outcome in result.outcomes:
        label = _OUTCOME_LABELS.get(outcome.status, outcome.status)
        line = f"  {outcome.step_name}: {label}"
        if outcome.detail:
            line += f" — {outcome.detail}"
        logger.info(line)
        if not outcome.ran:
            continue
        report = write_step_report(
            problem_dir, outcome, status_label=label, command_line=command_line
        )
        summary = [note_counts(outcome.notes)]
        if outcome.compile_errors:
            summary.append(f"{len(outcome.compile_errors)} compile error(s)")
        if outcome.charset_errors:
            summary.append(f"{len(outcome.charset_errors)} file(s) with non-ASCII characters")
        if outcome.removed or outcome.renamed:
            summary.append(f"{len(outcome.removed) + len(outcome.renamed)} removed/renamed")
        summary = [part for part in summary if part]
        logger.info(f"      report: {report}" + (f" ({'; '.join(summary)})" if summary else ""))
        for detail_line in _outcome_detail_lines(outcome):
            _log_to_file(file_handler, detail_line)

    if result.stopped_uncertain:
        logger.info(f"  ! pipeline stopped for '{result.problem_id}' — see the step above")


def _note_lines(notes) -> list[str]:
    return [
        f"      [{note.get('kind', '?')}] {note.get('field', '?')}: {note.get('explanation', '')}"
        for note in notes
    ]


def _outcome_detail_lines(outcome) -> list[str]:
    """The step's notes and files as lines — for the problem's log file."""
    lines = _note_lines(outcome.notes)
    for name, stderr in outcome.compile_errors.items():
        lines.append(f"      compile error: {name}: {stderr.strip()}")
    for name, message in outcome.charset_errors.items():
        lines.append(f"      {name}: {message}")
    for mark, reason in outcome.removed:
        lines.append(f"      removed (review and delete by hand): {mark} — {reason}")
    for old_path, new_path, reason in outcome.renamed:
        lines.append(f"      renamed: {old_path} -> {new_path} — {reason}")
    if outcome.backup_dir is not None:
        lines.append(f"      previous versions of overwritten files: {outcome.backup_dir}")
    # One line per reason: for a response not applied in full, it's
    # shared by all of its files.
    by_reason: dict[str, list[str]] = {}
    for filename, reason in outcome.conflicts:
        by_reason.setdefault(reason, []).append(filename)
    for reason, filenames in by_reason.items():
        lines.append(f"      not applied: {', '.join(filenames)} — {reason}")
    if outcome.conflicts_dir is not None:
        lines.append(f"      model versions of not applied files: {outcome.conflicts_dir}")
    return lines


# How many lines with `init --draft` marks to show in the warning.
_REVIEW_MARKS_SHOWN = 10


def _review_marks_warning(args: argparse.Namespace) -> list[str]:
    """A warning about unreviewed `init --draft` marks in the spec
    (`spec.spec_review_marks`) — as lines; empty if there are no marks. Doesn't
    stop the command: whether to check each one is up to the author."""
    path = spec_path(args.problem_id, problems_dir=args.problems_dir)
    marks = spec_review_marks(path)
    if not marks:
        return []
    lines = [
        f"warning: {path} still has {len(marks)} PROPOSED / SUGGESTION mark(s) from "
        "init --draft — the steps take the spec as the author's decisions: review these "
        "values and remove the marks"
    ]
    lines += [f"  line {number}: {line}" for number, line in marks[:_REVIEW_MARKS_SHOWN]]
    if len(marks) > _REVIEW_MARKS_SHOWN:
        lines.append(f"  ... and {len(marks) - _REVIEW_MARKS_SHOWN} more")
    return lines


def cmd_llm_generate(args: argparse.Namespace) -> int:
    return _run_llm_pipeline(
        args, force=args.force, fresh=args.fresh, survivor_rounds=args.survivor_rounds
    )


def cmd_llm_tutorial(args: argparse.Namespace) -> int:
    draft = tutorial_draft.draft_path(args.problem_id, problems_dir=args.problems_dir)
    if not draft.is_file():
        print(f"error: {draft} does not exist — write the tutorial draft first", file=sys.stderr)
        return 1
    args.step = tutorial_draft.STEP_NAME
    return _run_llm_pipeline(
        args, force=args.force, fresh=args.fresh, tutorial_refs=[] if args.no_ref else args.ref
    )


def cmd_llm_critic(args: argparse.Namespace) -> int:
    """`llm critic`: the critic over the spec as it is (`spec_critic.review_spec`) —
    the findings go to `llm_reports/spec_critic/<lens>.md`, the spec isn't changed. No cache:
    every run calls the model."""
    import yaml

    from orchestrator.spec_critic import review_spec

    path = spec_path(args.problem_id, problems_dir=args.problems_dir)
    if not path.is_file():
        print(f"error: {path} does not exist — nothing to review", file=sys.stderr)
        return 1
    spec_text = path.read_text(encoding="utf-8")
    try:
        yaml.safe_load(spec_text)
    except yaml.YAMLError as exc:
        print(f"error: {path} is not valid YAML — fix it first: {exc}", file=sys.stderr)
        return 1

    logger = _configure_console_logging()
    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, LLM_GENERATION_LOG, args.command_line
    ) as file_handler:
        try:
            outcome = review_spec(
                spec_text,
                lenses=args.lens,
                prompts_dir=args.prompts_dir,
                templates_dir=args.templates_dir,
                examples_dir=args.base_dir / EXAMPLES_DIR,
            )
        except LLMCallError as exc:
            logger.error(f"the critic call failed: {exc}")
            return 1
        except Exception:
            _log_traceback_to_file(
                file_handler, f"unhandled error while reviewing the spec of '{args.problem_id}'"
            )
            raise
        label = f"{len(outcome.findings)} finding(s)" if outcome.findings else "no findings"
        logger.info(
            f"=== {args.problem_id}: spec critic ({', '.join(outcome.lenses)}) — {label} ==="
        )
        reports = write_spec_critic_reports(
            Path(args.problems_dir) / args.problem_id,
            outcome,
            spec_path=path,
            command_line=args.command_line,
        )
        for report in reports:
            logger.info(f"      report: {report}")
        for finding in outcome.findings:
            owner = finding.owner or "unresolved"
            _log_to_file(
                file_handler,
                f"      [{finding.lens} {finding.severity} {finding.action}] {finding.field} "
                f"({owner}): "
                f"{finding.why}",
            )
        for line in _note_lines(outcome.notes):
            logger.warning(line)
    # The notes are problems of the run itself (an invalid critique, a failed lens):
    # the calls were spent, but the review is incomplete.
    return 1 if outcome.notes else 0


def cmd_llm_apply(args: argparse.Namespace) -> int:
    return _run_llm_pipeline(args, apply_pending=True)


def _run_llm_pipeline(args: argparse.Namespace, **mode) -> int:
    """`llm generate` / `llm apply`: running the steps with a log in
    `llm_generation.log`; `mode` — the `run_pipeline` mode."""
    logger = _configure_console_logging()

    if args.step is not None and args.step not in LLM_STEPS:
        print(f"unknown step '{args.step}', allowed: {', '.join(LLM_STEPS)}", file=sys.stderr)
        return 2

    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, LLM_GENERATION_LOG, args.command_line
    ) as file_handler:
        if not mode.get("apply_pending"):
            for line in _review_marks_warning(args):
                logger.warning(line)
        try:
            result = run_pipeline(
                args.problem_id,
                **mode,
                only_step=args.step,
                prompts_dir=args.prompts_dir,
                problems_dir=args.problems_dir,
                templates_dir=args.templates_dir,
            )
        except Exception:
            _log_traceback_to_file(
                file_handler, f"unhandled error while processing '{args.problem_id}'"
            )
            raise
        _print_pipeline_result(
            logger,
            result,
            file_handler,
            Path(args.problems_dir) / args.problem_id,
            args.command_line,
        )
    return 0 if result.ok else 1


def _print_step_statuses(problem_id: str, statuses, spec_error: str | None) -> bool:
    """Prints `step: state — details` (or the spec error); for a step with
    a short form of the details (`brief`) — that one. Returns whether there was a spec
    error."""
    print(f"=== {problem_id} ===")
    if spec_error is not None:
        print(spec_error)
        return True

    for step_status in statuses:
        line = f"  {step_status.step_name}: {step_status.state}"
        detail = step_status.brief or step_status.detail
        if detail:
            line += f" — {detail}"
        print(line)
    return False


def cmd_llm_status(args: argparse.Namespace) -> int:
    statuses, spec_error = compute_step_statuses(
        args.problem_id,
        prompts_dir=args.prompts_dir,
        problems_dir=args.problems_dir,
        templates_dir=args.templates_dir,
    )
    any_error = _print_step_statuses(args.problem_id, statuses, spec_error)
    for line in _review_marks_warning(args):
        print(line)
    return 1 if any_error else 0


def cmd_llm_accept(args: argparse.Namespace) -> int:
    """Accepts the current artifacts of outdated steps without calling the model (see
    `pipeline.accept_steps`). Exit code 1 — a spec error or at least one
    step wasn't accepted (or is blocked)."""
    statuses, spec_error = accept_steps(
        args.problem_id,
        only_step=args.step,
        prompts_dir=args.prompts_dir,
        problems_dir=args.problems_dir,
        templates_dir=args.templates_dir,
    )
    any_not_accepted = any(
        status.state in (STATUS_NOT_ACCEPTED, STATUS_BLOCKED) for status in statuses
    )
    any_error = _print_step_statuses(args.problem_id, statuses, spec_error)
    # What changed in the input of the accepted steps — in full in the report, in the console
    # only briefly.
    reported = [
        status for status in statuses if status.state in (STATUS_ACCEPTED, STATUS_NOT_ACCEPTED)
    ]
    if reported:
        report = write_accept_report(
            Path(args.problems_dir) / args.problem_id, reported, command_line=args.command_line
        )
        print(f"  report: {report}")
    return 1 if any_error or any_not_accepted else 0


def _print_polygon_outcomes(outcomes) -> None:
    for outcome in outcomes:
        line = f"  {outcome.step_name}: {outcome.status}"
        if outcome.detail:
            line += f" — {outcome.detail}"
        print(line)


def _shorten_error(detail: str) -> str:
    """The first line of the error text, no longer than `_CONSOLE_ERROR_MAX_LEN`."""
    first_line = detail.strip().splitlines()[0] if detail.strip() else ""
    if len(first_line) > _CONSOLE_ERROR_MAX_LEN:
        return first_line[: _CONSOLE_ERROR_MAX_LEN - 1] + "…"
    return first_line


def _print_progress(name: str) -> None:
    """The `polygon push`/`pull` progress line before a step or a problem part
    (`flush` — so that the progress is visible also when the output goes to a pipe/file)."""
    print(f"  {name}...", flush=True)


def _print_groups(groups: list[tuple[str, list[str], str]]) -> bool:
    """The summary of `polygon push`/`pull` by groups: `(title, lines,
    hint)`; empty groups aren't printed, the hint — after the lines, if
    it isn't empty. Returns whether there was anything to print."""
    printed = False
    for title, lines, hint in groups:
        if not lines:
            continue
        print(f"\n=== {title} ===")
        for line in lines:
            print(line)
        if hint:
            print(f"({hint})")
        printed = True
    return printed


def _polygon_error_line(outcome, log_path: Path) -> str:
    """A polygon step's error in one line: the orchestrator's own text — in full,
    a Polygon API response and the like — briefly, the full text in `polygon.log`."""
    if outcome.full_detail:
        return f"{outcome.step_name}: {outcome.detail}"
    return f"{outcome.step_name}: {_shorten_error(outcome.detail)} (details: {log_path})"


def cmd_polygon_push(args: argparse.Namespace) -> int:
    """Running the polygon steps (`run_polygon_pipeline`). Along the way — a line per
    step, at the end — the groups: what was uploaded, the steps' notes, the error and the steps
    that didn't upload theirs because of it (not `done` by
    `compute_polygon_step_statuses`), warnings. Steps with nothing
    to upload aren't printed in the summary."""
    warnings: list[str] = []
    logger = _configure_console_logging(warnings_into=warnings)
    log_path = Path(args.problems_dir) / args.problem_id / POLYGON_LOG

    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        result = run_polygon_pipeline(
            args.problem_id,
            only_step=args.step,
            force=args.force,
            no_commit=args.no_commit,
            problems_dir=args.problems_dir,
            on_step_start=_print_progress,
        )
    if result.spec_error is not None:
        print(result.spec_error, file=sys.stderr)
        return 1

    outcomes = result.outcomes
    pushed = [name for outcome in outcomes for name in describe_keys(outcome.uploaded)]
    errors = [
        _polygon_error_line(outcome, log_path)
        for outcome in outcomes
        if outcome.status == POLYGON_OUTCOME_ERROR
    ]
    not_pushed = []
    if errors:
        failed = outcomes[-1].step_name
        remaining = (
            [failed] if args.step else POLYGON_STEP_ORDER[POLYGON_STEP_ORDER.index(failed) :]
        )
        not_pushed = [
            status.step_name
            for status in compute_polygon_step_statuses(
                args.problem_id, problems_dir=args.problems_dir
            )
            if status.step_name in remaining and status.status != POLYGON_STATUS_DONE
        ]
    state = load_polygon_state(args.problem_id, problems_dir=args.problems_dir)
    if not pushed or state is None:
        committed = ""
    elif args.no_commit:
        committed = (
            f"not committed: commit on Polygon id={state.polygon_id}, then run `polygon pull`"
        )
    else:
        committed = f"committed on Polygon id={state.polygon_id}, revision {state.revision}"
    printed = _print_groups(
        [
            ("pushed", pushed, committed),
            ("notes", [f"{o.step_name}: {o.notice}" for o in outcomes if o.notice], ""),
            ("errors", errors, ""),
            (
                "not pushed",
                not_pushed,
                "push stopped on the error above; `polygon status` shows what these steps "
                "have to upload",
            ),
            ("warnings", warnings, ""),
        ]
    )
    if not printed:
        print("\nnothing to push: Polygon is up to date")
    return 0 if result.ok else 1


def cmd_polygon_status(args: argparse.Namespace) -> int:
    _print_polygon_outcomes(
        compute_polygon_step_statuses(args.problem_id, problems_dir=args.problems_dir)
    )
    return 0


def cmd_local_check(args: argparse.Namespace) -> int:
    """Without the model and without the network checks the problem's files: the statement's formatting
    (`statement/*.tex`: Markdown-style lists, quotes other than `<<…>>` —
    `orchestrator/checks/statement_check.py`) and the test-script(s) — expands
    them and prints identical lines and lines Polygon won't accept
    (quotes, an extra `>`), pointing to the script lines that printed them
    (`orchestrator/checks/test_script_check.py`). No spec needed —
    what's on disk is checked; what's missing is skipped with a note."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    if not problem_dir.is_dir():
        print(f"no problem directory {problem_dir}", file=sys.stderr)
        return 1
    failed = False
    try:
        tex_files = statement_files(problem_dir)
    except ProblemPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if tex_files:
        failed |= not _check_statement(problem_dir, tex_files)
    else:
        print(f"no statement ({STATEMENT_DIRNAME}/<language>/*.tex) — skipped")
    scripts_ok = _check_scripts(problem_dir)
    failed |= not scripts_ok
    if args.full:
        if scripts_ok:
            failed |= not _check_generated_tests(problem_dir, Path(args.templates_dir))
        else:
            print("tests are not generated: fix the test script first")
    return 1 if failed else 0


def _check_generated_tests(problem_dir: Path, templates_dir: Path) -> bool:
    """`local check --full`: tests from the script (with a cache), the validator on them and
    a search for tests identical in content (`orchestrator/checks/local_tests.py`).
    Identical tests are only shown, they aren't considered an error."""

    def progress(line: str) -> None:
        print(line, flush=True)

    generated = generate_tests(problem_dir, templates_dir, progress=progress)
    print(
        f"tests: {len(generated.tests)} ({generated.generated} generated, "
        f"{generated.reused} from cache, {generated.removed} removed), "
        f"see {TESTS_CACHE_DIRNAME}/{MANIFEST_FILENAME}"
    )
    errors = list(generated.errors)
    validator_errors, validated = validate_tests(problem_dir, templates_dir, generated.tests)
    errors += validator_errors
    if not validated:
        print(f"no {VALIDATOR_FILENAME}: tests are not validated")
    elif not validator_errors:
        print(f"{VALIDATOR_FILENAME} accepts all {len(generated.tests)} test(s)")
    equal = find_equal_tests(problem_dir, generated.tests)
    print(format_equal_tests(equal, generated.tests) if equal else "no equal tests")
    for error in errors:
        print(error)
    return not errors


def cmd_local_clear(args: argparse.Namespace) -> int:
    """Deletes the problem's local check caches (`.tests_cache/`). Doesn't touch the model's step
    cache, the Polygon state and backups."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    if not problem_dir.is_dir():
        print(f"no problem directory {problem_dir}", file=sys.stderr)
        return 1
    removed = clear_local_caches(problem_dir)
    for name, size in removed:
        print(f"removed {name}/ ({size / (1024 * 1024):.1f} MB)")
    if not removed:
        print("nothing to clear")
    return 0


def cmd_local_run(args: argparse.Namespace) -> int:
    """Without the model and without the network builds the problem's validator and solutions and
    runs them on the examples (`orchestrator/checks/solutions_run.py`). Along
    the way — notes and a line per solution being run, at the end — the groups,
    as in `polygon push`: expected and unexpected verdicts, solutions not run,
    warnings (long-running solutions without the `tl` verdict), errors.
    Exit code 1 — if there are unexpected verdicts, solutions not run
    or errors."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    if not problem_dir.is_dir():
        print(f"no problem directory {problem_dir}", file=sys.stderr)
        return 1
    result = run_solutions(
        problem_dir,
        Path(args.templates_dir),
        progress=lambda line: print(line, flush=True),
        full=args.full,
    )
    _print_groups(result.groups())
    return 1 if result.failed else 0


def _check_scripts(problem_dir: Path) -> bool:
    scripts = [problem_dir / name for name in SCRIPT_FILENAMES if (problem_dir / name).is_file()]
    if not scripts:
        print(f"no test script (expected one of: {', '.join(SCRIPT_FILENAMES)}) — skipped")
        return True
    ok = True
    for script in scripts:
        try:
            result = check_script(script.read_text(encoding="utf-8"), script.name)
        except ScriptCheckUnavailable as exc:
            print(f"{script.name}: check unavailable — {exc}", file=sys.stderr)
            ok = False
            continue
        except ScriptRenderError as exc:
            print(f"{script.name}: FreeMarker failed to expand the script:\n{exc}")
            ok = False
            continue
        if result.failed:
            print(result.report)
            ok = False
        else:
            print(f"{script.name}: ok, no identical or invalid lines")
    return ok


def _check_statement(problem_dir: Path, tex_files: list[tuple[Path, str]]) -> bool:
    issues = [
        issue
        for path, language in tex_files
        for issue in check_statement_text(
            path.read_text(encoding="utf-8"),
            path.relative_to(problem_dir).as_posix(),
            quotes=language == QUOTES_LANGUAGE,
        )
    ]
    if not issues:
        quotes = (
            ", all quotes are <<...>>"
            if any(language == QUOTES_LANGUAGE for _, language in tex_files)
            else ""
        )
        print(f"{STATEMENT_DIRNAME}: ok, no Markdown lists{quotes}")
        return True
    print(f"{STATEMENT_DIRNAME}: {len(issues)} formatting issue(s):")
    for issue in issues:
        print(f"  {issue.format()}")
    if any(issue.fixable for issue in issues):
        print(f"run 'local fix' to fix them (old versions go to {FIX_BACKUP_DIRNAME}/)")
    return False


def cmd_local_fix(args: argparse.Namespace) -> int:
    """Fixes in the statement what `local check` finds and what can be
    fixed mechanically: Markdown-style lists -> `itemize`/`enumerate`,
    quotes -> `<<…>>`. Previous versions of changed files — in the problem directory's
    `.fix_backup/<time>/`. Without the model and without the network; push
    uploads the changed files as usual. Exit code 1 — if there are problems
    left that have to be fixed by hand (unpaired quotes)."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    try:
        tex_files = statement_files(problem_dir)
    except ProblemPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not tex_files:
        print(
            f"no statement in {problem_dir / STATEMENT_DIRNAME} (<language>/*.tex)",
            file=sys.stderr,
        )
        return 1
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = problem_dir / FIX_BACKUP_DIRNAME / stamp
    changed = False
    remaining = []
    for path, language in tex_files:
        name = path.relative_to(problem_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        quotes = language == QUOTES_LANGUAGE
        fixed = fix_statement_text(text, quotes=quotes)
        if fixed != text:
            target = backup_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            path.write_text(fixed, encoding="utf-8")
            changed = True
            print(f"  {name}: fixed")
        remaining.extend(check_statement_text(fixed, name, quotes=quotes))
    if changed:
        print(f"old versions: {backup_dir}")
    else:
        print(f"{STATEMENT_DIRNAME}: nothing to fix")
    if remaining:
        print("not fixed, fix by hand:")
        for issue in remaining:
            print(f"  {issue.format()}")
        return 1
    return 0


def cmd_polygon_pull(args: argparse.Namespace) -> int:
    """Binding to a problem existing on Polygon, comparing the local
    files with it and downloading the missing ones and those changed only on Polygon / with
    `--overwrite` — also those changed on both sides (see
    `orchestrator/polygon/pull.py`). No spec needed. Along the way — a line per
    problem part, at the end — groups by what happened to the files;
    what matches Polygon and what's absent on both sides isn't printed.
    The log — in the same `polygon.log` as for `polygon push`."""
    warnings: list[str] = []
    logger = _configure_console_logging(warnings_into=warnings)
    log_path = Path(args.problems_dir) / args.problem_id / POLYGON_LOG

    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        try:
            result = pull_problem(
                args.problem_id,
                polygon_id=args.polygon_id,
                overwrite=args.overwrite,
                problems_dir=args.problems_dir,
                on_part=_print_progress,
            )
        except PolygonStepError as exc:
            logger.error("[pull] %s", exc)
            print(str(exc), file=sys.stderr)
            return 1
        except PolygonApiError as exc:
            logger.error("[pull] %s", exc)
            print(f"{_shorten_error(str(exc))} (details: {log_path})", file=sys.stderr)
            return 1

    def items(*statuses: str) -> list:
        return [item for item in result.items if item.status in statuses]

    printed = _print_groups(
        [
            ("pulled", [i.name for i in items(PULL_ITEM_DOWNLOADED, PULL_ITEM_UPDATED)], ""),
            (
                "pulled over local changes (--overwrite)",
                [i.name for i in items(PULL_ITEM_OVERWRITTEN)],
                "",
            ),
            (
                "will be pushed",
                [
                    i.name + (f" (if {i.push_condition})" if i.push_condition else "")
                    for i in items(PULL_ITEM_KEPT, PULL_ITEM_LOCAL_ONLY)
                ],
                "",
            ),
            (
                "conflicts",
                [_pull_conflict_line(i) for i in items(PULL_ITEM_CONFLICT, PULL_ITEM_DIFFERS)],
                "local versions kept, the next push uploads them; "
                "`polygon pull --overwrite` takes the Polygon versions instead",
            ),
            (
                "errors",
                [f"{i.name}: {_shorten_error(i.detail)}" for i in items(PULL_ITEM_ERROR)],
                f"details: {log_path}",
            ),
            ("warnings", warnings, ""),
        ]
    )
    if not printed:
        print("\nnothing changed: up to date with Polygon")
    print(f"\n{result.link_detail}, revision {result.revision}")
    if result.backup_dir is not None:
        print(f"replaced local files are backed up in {result.backup_dir}")
    return 0 if result.ok else 1


def _pull_conflict_line(item) -> str:
    """A pull conflict: what changed — both sides or unknown (no base) —
    and pull's explanation (where the version on Polygon came from, how the TL/ML differ)."""
    reason = "changed on both sides" if item.status == PULL_ITEM_CONFLICT else "no sync base"
    return f"{item.name} — {reason}" + (f"; {item.detail}" if item.detail else "")


def cmd_init(args: argparse.Namespace) -> int:
    """Creates a problem directory with a spec (see `spec.prepare_spec_init`): from the
    template, as a copy of the base problem's spec (`--ref`) or — with `--draft` — by the model
    from the author's draft (`orchestrator/spec_from_draft.py`, the only
    variant of `init` that calls the model). The spec already exists — with `--draft`
    the model revises it by the changed draft
    (`spec_from_draft.prepare_spec_revision`). Polygon isn't called."""
    try:
        draft_path = (
            None if args.draft is None else find_draft(args.base_dir / DRAFTS_DIR, args.draft)
        )
        if (
            draft_path is not None
            and spec_path(args.problem_id, problems_dir=args.problems_dir).exists()
        ):
            return _revise_spec_from_draft(args, draft_path)
        init = prepare_spec_init(
            args.problem_id,
            problems_dir=args.problems_dir,
            templates_dir=args.templates_dir,
            reference=args.ref,
        )
    except (DraftError, SpecInitError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if draft_path is None:
        created = write_spec(init, init.base_text)
        if init.reference is None:
            print(f"created {created} — fill it in (format: docs/SPEC_FORMAT.md)")
        else:
            print(f"created {created} as a copy of the spec of {init.reference} — edit it")
            _print_spec_validation_errors(
                spec_validation_errors(created, expected_id=init.problem_id), print
            )
        return 0
    return _init_from_draft(args, init, draft_path)


def _revise_spec_from_draft(args: argparse.Namespace, draft_path: Path) -> int:
    """`init --draft` for a problem with a spec: revising the spec if the draft
    changed since the previous copy (or `--force`). `--ref` — only on
    creation: the base problem is already in the spec's references."""
    from orchestrator.spec_from_draft import draft_unchanged, prepare_spec_revision

    if args.ref is not None:
        raise SpecInitError(
            f"{spec_path(args.problem_id, problems_dir=args.problems_dir)} already exists: "
            "--ref is only for creating a spec (edit its references instead)"
        )
    init, revision = prepare_spec_revision(args.problem_id, problems_dir=args.problems_dir)
    if not args.force and draft_unchanged(revision, draft_path):
        print(
            f"{draft_path} has not changed since {init.path} was written from it — "
            "spec not touched (--force to revise it anyway)"
        )
        return 0
    return _init_from_draft(args, init, draft_path, revision=revision)


def _print_spec_validation_errors(errors: list[str], output) -> None:
    if errors:
        output("the spec does not pass validation yet (docs/SPEC_FORMAT.md):")
        for error in errors:
            output(f"  - {error}")


def _init_from_draft(args: argparse.Namespace, init, draft_path: Path, *, revision=None) -> int:
    """`init --draft`: the spec is written (with `revision` — revised) by the model, the log —
    in the problem's `llm_generation.log`."""
    from orchestrator.spec_from_draft import generate_spec_from_draft

    logger = _configure_console_logging()
    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, LLM_GENERATION_LOG, args.command_line
    ) as file_handler:
        try:
            result = generate_spec_from_draft(
                init,
                draft_path,
                revision=revision,
                prompts_dir=args.prompts_dir,
                templates_dir=args.templates_dir,
                examples_dir=args.base_dir / EXAMPLES_DIR,
            )
        except LLMCallError as exc:
            logger.error(f"the model call failed: {exc} — spec not touched")
            return 1
        except Exception:
            _log_traceback_to_file(
                file_handler, f"unhandled error while writing the spec of '{args.problem_id}'"
            )
            raise

        critic_failed = False
        if _should_run_critic(args, result):
            try:
                _run_critic(args, init, draft_path, result)
                if result.critique.failed_lenses:
                    critic_failed = True
                    failed = ", ".join(result.critique.failed_lenses)
                    logger.error(f"the critic call failed for the lens(es) {failed}")
            except LLMCallError as exc:
                critic_failed = True
                logger.error(f"the critic call failed: {exc} — the spec is kept without review")
                result.notes.append(
                    {"field": "spec_critic", "kind": "uncertain", "explanation": f"failed: {exc}"}
                )
            except Exception:
                _log_traceback_to_file(
                    file_handler, f"unhandled error while reviewing the spec of '{args.problem_id}'"
                )
                raise

        label = _OUTCOME_LABELS.get(result.status, result.status)
        logger.info(f"=== {init.problem_id}: spec from draft {draft_path} — {label} ===")
        report = write_spec_from_draft_report(
            Path(args.problems_dir) / args.problem_id,
            result,
            status_label=label,
            command_line=args.command_line,
            draft_path=draft_path,
        )
        counts = note_counts(result.notes)
        logger.info(f"      report: {report}" + (f" ({counts})" if counts else ""))
        for line in _note_lines(result.notes):
            _log_to_file(file_handler, line)
        if result.path is None:
            logger.info("  ! no spec written — see the notes above, fix the draft and run again")
            return 1
        if revision is None:
            logger.info(f"created {result.path} — review the PROPOSED / SUGGESTION marks")
        elif result.unchanged:
            logger.info(f"{result.path} needs no changes for the new draft — kept as is")
        else:
            logger.info(f"revised {result.path} — review the changes and new marks")
        logger.info(f"draft copied to {result.draft_copy}")
        if result.critique is not None:
            logger.info(_critique_line(result.critique))
        if result.backup_dir is not None:
            logger.info(f"previous versions are backed up in {result.backup_dir}")
        _print_spec_validation_errors(result.validation_errors, logger.info)
    return 1 if critic_failed else 0


def _should_run_critic(args: argparse.Namespace, result) -> bool:
    """The critic runs over a spec the model has just written or changed."""
    from orchestrator.steps.base import STATUS_UNCERTAIN

    return (
        not args.no_critic
        and result.path is not None
        and not result.unchanged
        and result.status != STATUS_UNCERTAIN
    )


def _run_critic(args: argparse.Namespace, init, draft_path: Path, result) -> None:
    from orchestrator.spec_critic import apply_critique, critique_spec

    outcome = critique_spec(
        init,
        Path(draft_path).read_text(encoding="utf-8"),
        result.path.read_text(encoding="utf-8"),
        generator_notes=result.notes,
        rounds=args.critic_rounds,
        lenses=args.critic_lens,
        prompts_dir=args.prompts_dir,
        templates_dir=args.templates_dir,
        examples_dir=args.base_dir / EXAMPLES_DIR,
    )
    apply_critique(init, result, outcome)


def _critique_line(outcome) -> str:
    """The console summary of the critic: findings by outcome."""
    from collections import Counter

    if not outcome.findings:
        return "critic: no findings"
    counts = Counter(f.outcome for f in outcome.findings)
    summary = ", ".join(f"{count} {name}" for name, count in counts.items())
    changed = " — the spec was changed" if outcome.changed else ""
    return f"critic: {len(outcome.findings)} finding(s): {summary}{changed} (see the report)"
