"""Реализация команд CLI: `init`, `llm generate|apply|status|accept`,
`polygon push|status|check|fix|pull`.

Парсер аргументов и входная точка — в `cli.py`; этот модуль он импортирует
только после разбора аргументов, потому что отсюда тянутся оба пайплайна (а
с ними pydantic и requests) — см. докстринг `cli.py`.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orchestrator.pipeline import (
    OUTCOME_CACHE_HIT,
    OUTCOME_CONFIRMED,
    OUTCOME_CONFLICT,
    OUTCOME_ERROR,
    OUTCOME_NO_PENDING,
    OUTCOME_PROPOSED,
    OUTCOME_SKIPPED_OPTIONAL,
    OUTCOME_UNCERTAIN,
    STATUS_ACCEPTED,
    STATUS_BLOCKED,
    STATUS_NOT_ACCEPTED,
    accept_steps,
    compute_step_statuses,
    run_pipeline,
)
from orchestrator.polygon.pipeline import OUTCOME_ERROR as POLYGON_OUTCOME_ERROR
from orchestrator.polygon.pipeline import (
    compute_polygon_step_statuses,
    run_polygon_pipeline,
)
from orchestrator.polygon.client import PolygonApiError
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
from orchestrator.polygon.sync import describe_keys
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_test_script import SCRIPT_FILENAMES
from orchestrator.checks.statement_check import (
    FIX_BACKUP_DIRNAME,
    STATEMENT_DIRNAME,
    check_text as check_statement_text,
    fix_text as fix_statement_text,
    statement_files,
)
from orchestrator.checks.solutions_run import run_solutions
from orchestrator.checks.test_script_check import (
    ScriptCheckUnavailable,
    ScriptRenderError,
    check_script,
)
from orchestrator.drafts import DRAFTS_DIR, DraftError, find_draft
from orchestrator.llm_report import (
    note_counts,
    write_accept_report,
    write_spec_from_draft_report,
    write_step_report,
)
from orchestrator.problem_paths import EXAMPLES_DIR
from orchestrator.spec import (
    SpecInitError,
    prepare_spec_init,
    spec_path,
    spec_review_marks,
    spec_validation_errors,
    write_spec,
)
from orchestrator.step_order import POLYGON_STEP_ORDER, STEP_ORDER

_OUTCOME_LABELS = {
    OUTCOME_CACHE_HIT: "cache hit",
    OUTCOME_CONFIRMED: "confirmed",
    OUTCOME_PROPOSED: "proposed (PROPOSED, REVIEW ME)",
    OUTCOME_UNCERTAIN: "uncertain",
    OUTCOME_SKIPPED_OPTIONAL: "skipped (optional)",
    OUTCOME_NO_PENDING: "nothing to apply",
    OUTCOME_ERROR: "error",
    OUTCOME_CONFLICT: "conflict",
}

# Логгер пакета — на него вешается и консольный вывод (см.
# `_configure_console_logging`), и по-задачные лог-файлы
# `problems/<id>/llm_generation.log` / `problems/<id>/polygon.log` (см.
# `_problem_log_handler`), см. CLAUDE.md, задача "лог выполнения". Дочерние
# логгеры (например, `orchestrator.llm_clients.anthropic_client`, откуда
# идёт `_log_usage`) пишут через propagate в этот же логгер — отдельно
# настраивать их не нужно.
LOGGER_NAME = "orchestrator"

# Отдельные файлы на генерацию и на Polygon: `polygon push` и `llm generate` запускаются
# независимо, и общий файл, перезаписываемый каждым прогоном, терял бы лог
# одного при запуске другого.
LLM_GENERATION_LOG = "llm_generation.log"
POLYGON_LOG = "polygon.log"

# Заголовок прогона в лог-файле задачи; `%s` — команда целиком.
_RUN_HEADER = "===== run started: orchestrator %s ====="

# Сколько символов текста ошибки polygon-шага печатать в консоль: `comment`
# от Polygon бывает многострочным (например, лог компиляции валидатора) —
# целиком он пишется в `polygon.log`. Собственные ошибки оркестратора
# (`PolygonStepError`) не обрезаются: подсказка, что делать, у них в конце.
_CONSOLE_ERROR_MAX_LEN = 200

_console_handler: logging.Handler | None = None


class _ListHandler(logging.Handler):
    """Копит текст записей в список — CLI печатает его сам, в своём месте вывода."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _configure_console_logging(*, warnings_into: list[str] | None = None) -> logging.Logger:
    """Настраивает логгер пакета так, чтобы INFO-сообщения были видны в
    консоли — до этого `logger.info(...)` в `_log_usage` никуда не выводился,
    потому что для логгера не было ни уровня, ни handler'а.

    Пересоздаёт handler при каждом вызове (а не один раз при импорте модуля):
    `logging.StreamHandler()` фиксирует `sys.stdout` в момент создания, а
    `capsys` в тестах подменяет `sys.stdout` на время теста — handler,
    созданный при импорте, писал бы мимо этой подмены.

    `warnings_into` — для `polygon push`/`pull`: в консоль не идёт ничего,
    WARNING копятся в этот список (CLI печатает их группой в итоге, см.
    `_print_groups`), а INFO (каждый HTTP-запрос, сообщения шагов) и ERROR
    (полный текст ошибки шага с traceback) — только в `polygon.log`.
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
    """Заводит `problems/<problem_id>/<log_name>` на время обработки одного
    `problem_id` — открывается на дозапись: лог накапливает историю
    прогонов, каждый прогон начинается с заголовка с командой (см.
    `_RUN_HEADER`) — по нему удобно искать нужный запуск, а время в каждой
    строке показывает длительность шагов. Handler добавляется к логгеру
    перед прогоном шагов и снимается сразу после, через try/finally — чтобы
    необработанное исключение не оставило handler висящим.

    Отдаёт сам handler — вызывающий код использует его напрямую в
    `_log_traceback_to_file` для необработанных исключений (см. там же,
    почему это не идёт через обычный `logger.exception`).
    """
    problem_dir = Path(problems_dir) / problem_id
    problem_dir.mkdir(parents=True, exist_ok=True)
    log_path = problem_dir / log_name
    needs_separator = log_path.exists() and log_path.stat().st_size > 0
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    if needs_separator:
        handler.stream.write("\n")
    # Только в файл, мимо консольного handler'а — в консоли команда и так видна.
    _log_to_file(handler, _RUN_HEADER % command_line)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        handler.close()


def _log_to_file(handler: logging.Handler, message: str) -> None:
    """INFO-запись только в `handler` (лог-файл задачи), мимо консоли."""
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
    """Пишет текущий traceback (`sys.exc_info()`) в конкретный `handler`,
    минуя остальные handler'ы логгера — чтобы вывод в консоль при
    необработанном исключении остался ровно таким же, как раньше (traceback
    туда и так печатает сам Python при завершении процесса), а
    `problems/<id>/llm_generation.log` при этом не терял traceback (CLAUDE.md, задача
    "лог выполнения", пункт 4).
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
    """Итог `llm generate`/`apply`: в консоль — строка на шаг и путь к
    отчёту `llm_reports/<шаг>.md` отработавшего шага (заметки модели, файлы,
    диффы — там, см. `llm_report`); в лог-файл задачи — ещё и все заметки и
    файлы, чтобы история прогонов в нём оставалась полной."""
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
    """Заметки и файлы шага строками — для лог-файла задачи."""
    lines = _note_lines(outcome.notes)
    for name, stderr in outcome.compile_errors.items():
        lines.append(f"      compile error: {name}: {stderr.strip()}")
    for mark, reason in outcome.removed:
        lines.append(f"      removed (review and delete by hand): {mark} — {reason}")
    for old_path, new_path, reason in outcome.renamed:
        lines.append(f"      renamed: {old_path} -> {new_path} — {reason}")
    if outcome.backup_dir is not None:
        lines.append(f"      previous versions of overwritten files: {outcome.backup_dir}")
    # Одна строка на причину: у ответа, не применённого целиком, она
    # общая для всех его файлов.
    by_reason: dict[str, list[str]] = {}
    for filename, reason in outcome.conflicts:
        by_reason.setdefault(reason, []).append(filename)
    for reason, filenames in by_reason.items():
        lines.append(f"      not applied: {', '.join(filenames)} — {reason}")
    if outcome.conflicts_dir is not None:
        lines.append(f"      model versions of not applied files: {outcome.conflicts_dir}")
    return lines


# Сколько строк с пометками `init --draft` показывать в предупреждении.
_REVIEW_MARKS_SHOWN = 10


def _review_marks_warning(args: argparse.Namespace) -> list[str]:
    """Предупреждение о непроверенных пометках `init --draft` в спеке
    (`spec.spec_review_marks`) — строками; пусто, если пометок нет. Не
    останавливает команду: проверять ли каждую — решает автор."""
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
    return _run_llm_pipeline(args, force=args.force, fresh=args.fresh)


def cmd_llm_apply(args: argparse.Namespace) -> int:
    return _run_llm_pipeline(args, apply_pending=True)


def _run_llm_pipeline(args: argparse.Namespace, **mode) -> int:
    """`llm generate` / `llm apply`: прогон шагов с логом в
    `llm_generation.log`; `mode` — режим `run_pipeline`."""
    logger = _configure_console_logging()

    if args.step is not None and args.step not in STEP_ORDER:
        print(f"unknown step '{args.step}', allowed: {', '.join(STEP_ORDER)}", file=sys.stderr)
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
    """Печатает `шаг: состояние — подробности` (или ошибку спека); у шага с
    короткой формой подробностей (`brief`) — она. Возвращает, была ли ошибка
    спека."""
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
    """Принимает текущие артефакты устаревших шагов без вызова модели (см.
    `pipeline.accept_steps`). Код возврата 1 — ошибка спека или хотя бы один
    шаг не принят (или заблокирован)."""
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
    # Что изменилось во входе принятых шагов — целиком в отчёте, в консоли
    # только кратко.
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
    """Первая строка текста ошибки, не длиннее `_CONSOLE_ERROR_MAX_LEN`."""
    first_line = detail.strip().splitlines()[0] if detail.strip() else ""
    if len(first_line) > _CONSOLE_ERROR_MAX_LEN:
        return first_line[: _CONSOLE_ERROR_MAX_LEN - 1] + "…"
    return first_line


def _print_progress(name: str) -> None:
    """Строка прогресса `polygon push`/`pull` перед шагом или частью задачи
    (`flush` — чтобы прогресс был виден и при выводе в pipe/файл)."""
    print(f"  {name}...", flush=True)


def _print_groups(groups: list[tuple[str, list[str], str]]) -> bool:
    """Итог `polygon push`/`pull` по группам: `(заголовок, строки,
    подсказка)`; пустые группы не печатаются, подсказка — после строк, если
    не пуста. Возвращает, было ли что печатать."""
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
    """Ошибка polygon-шага одной строкой: свой текст оркестратора — целиком,
    ответ Polygon API и прочее — коротко, полный текст в `polygon.log`."""
    if outcome.full_detail:
        return f"{outcome.step_name}: {outcome.detail}"
    return f"{outcome.step_name}: {_shorten_error(outcome.detail)} (details: {log_path})"


def cmd_polygon_push(args: argparse.Namespace) -> int:
    """Прогон polygon-шагов (`run_polygon_pipeline`). По ходу — строка на
    шаг, в конце — группы: что загружено, заметки шагов, ошибка и шаги,
    которые из-за неё не загрузили своё (не `done` по
    `compute_polygon_step_statuses`), предупреждения. Шаги, которым нечего
    загружать, в итоге не печатаются."""
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
    committed = (
        f"committed on Polygon id={state.polygon_id}, revision {state.revision}"
        if pushed and state is not None
        else ""
    )
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
    """Без модели и без сети проверяет файлы задачи: оформление условия
    (`statement/*.tex`: списки в стиле Markdown, кавычки не `<<…>>` —
    `orchestrator/checks/statement_check.py`) и test-script(ы) — раскрывает
    их и печатает одинаковые строки и строки, которые Polygon не примет
    (кавычки, лишний `>`), с указанием строк скрипта, которые их напечатали
    (`orchestrator/checks/test_script_check.py`). Спек не нужен —
    проверяется то, что есть на диске; чего нет — пропускается с заметкой."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    if not problem_dir.is_dir():
        print(f"no problem directory {problem_dir}", file=sys.stderr)
        return 1
    failed = False
    tex_files = statement_files(problem_dir)
    if tex_files:
        failed |= not _check_statement(problem_dir, tex_files)
    else:
        print(f"no statement ({STATEMENT_DIRNAME}/*.tex) — skipped")
    failed |= not _check_scripts(problem_dir)
    return 1 if failed else 0


def cmd_local_run(args: argparse.Namespace) -> int:
    """Без модели и без сети собирает валидатор и решения задачи и
    прогоняет их на примерах (`orchestrator/checks/solutions_run.py`). По
    ходу — заметки и строка на каждое запускаемое решение, в конце — группы,
    как у `polygon push`: ожидаемые и неожиданные вердикты, не запущенные
    решения, предупреждения (долгие решения не с вердиктом `tl`), ошибки.
    Код возврата 1 — если есть неожиданные вердикты, не запущенные решения
    или ошибки."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    if not problem_dir.is_dir():
        print(f"no problem directory {problem_dir}", file=sys.stderr)
        return 1
    result = run_solutions(
        problem_dir, Path(args.templates_dir), progress=lambda line: print(line, flush=True)
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


def _check_statement(problem_dir: Path, tex_files: list[Path]) -> bool:
    issues = [
        issue
        for path in tex_files
        for issue in check_statement_text(
            path.read_text(encoding="utf-8"), path.relative_to(problem_dir).as_posix()
        )
    ]
    if not issues:
        print(f"{STATEMENT_DIRNAME}: ok, no Markdown lists, all quotes are <<...>>")
        return True
    print(f"{STATEMENT_DIRNAME}: {len(issues)} formatting issue(s):")
    for issue in issues:
        print(f"  {issue.format()}")
    if any(issue.fixable for issue in issues):
        print(f"run 'local fix' to fix them (old versions go to {FIX_BACKUP_DIRNAME}/)")
    return False


def cmd_local_fix(args: argparse.Namespace) -> int:
    """Исправляет в условии то, что `local check` находит и что можно
    исправить механически: списки в стиле Markdown -> `itemize`/`enumerate`,
    кавычки -> `<<…>>`. Прежние версии изменённых файлов — в
    `.fix_backup/<время>/` каталога задачи. Без модели и без сети; push
    загрузит изменённые файлы как обычно. Код возврата 1 — если остались
    проблемы, которые нужно исправить руками (непарные кавычки)."""
    problem_dir = Path(args.problems_dir) / args.problem_id
    tex_files = statement_files(problem_dir)
    if not tex_files:
        print(f"no statement in {problem_dir / STATEMENT_DIRNAME} (*.tex)", file=sys.stderr)
        return 1
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = problem_dir / FIX_BACKUP_DIRNAME / stamp
    changed = False
    remaining = []
    for path in tex_files:
        name = path.relative_to(problem_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        fixed = fix_statement_text(text)
        if fixed != text:
            target = backup_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            path.write_text(fixed, encoding="utf-8")
            changed = True
            print(f"  {name}: fixed")
        remaining.extend(check_statement_text(fixed, name))
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
    """Привязка к существующей на Polygon задаче, сравнение с ней локальных
    файлов и выгрузка недостающих и изменённых только на Polygon / с
    `--overwrite` — и изменённых с обеих сторон (см.
    `orchestrator/polygon/pull.py`). Спек не нужен. По ходу — строка на
    часть задачи, в конце — группы по тому, что случилось с файлами;
    совпадающее с Polygon и отсутствующее с обеих сторон не печатается.
    Лог — в тот же `polygon.log`, что и у `polygon push`."""
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
    """Конфликт pull: что менялось — обе стороны или неизвестно (нет базы) —
    и пояснение pull (откуда версия на Polygon, чем отличаются TL/ML)."""
    reason = "changed on both sides" if item.status == PULL_ITEM_CONFLICT else "no sync base"
    return f"{item.name} — {reason}" + (f"; {item.detail}" if item.detail else "")


def cmd_init(args: argparse.Namespace) -> int:
    """Создаёт каталог задачи со спеком (см. `spec.prepare_spec_init`): из
    шаблона, копией спека задачи-основы (`--ref`) или — с `--draft` — моделью
    по черновику автора (`orchestrator/spec_from_draft.py`, единственный
    вариант `init`, который вызывает модель). Polygon не вызывается."""
    try:
        draft_path = (
            None if args.draft is None else find_draft(args.base_dir / DRAFTS_DIR, args.draft)
        )
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
        spec_path = write_spec(init, init.base_text)
        if init.reference is None:
            print(f"created {spec_path} — fill it in (format: docs/SPEC_FORMAT.md)")
        else:
            print(f"created {spec_path} as a copy of the spec of {init.reference} — edit it")
            _print_spec_validation_errors(
                spec_validation_errors(spec_path, expected_id=init.problem_id), print
            )
        return 0
    return _init_from_draft(args, init, draft_path)


def _print_spec_validation_errors(errors: list[str], output) -> None:
    if errors:
        output("the spec does not pass validation yet (docs/SPEC_FORMAT.md):")
        for error in errors:
            output(f"  - {error}")


def _init_from_draft(args: argparse.Namespace, init, draft_path: Path) -> int:
    """`init --draft`: спек пишет модель, лог — в `llm_generation.log` задачи."""
    from orchestrator.spec_from_draft import generate_spec_from_draft

    logger = _configure_console_logging()
    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, LLM_GENERATION_LOG, args.command_line
    ) as file_handler:
        try:
            result = generate_spec_from_draft(
                init,
                draft_path,
                prompts_dir=args.prompts_dir,
                templates_dir=args.templates_dir,
                examples_dir=args.base_dir / EXAMPLES_DIR,
            )
        except Exception:
            _log_traceback_to_file(
                file_handler, f"unhandled error while writing the spec of '{args.problem_id}'"
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
        logger.info(f"created {result.path} — review the PROPOSED / SUGGESTION marks")
        logger.info(f"draft copied to {result.draft_copy}")
        _print_spec_validation_errors(result.validation_errors, logger.info)
    return 0
