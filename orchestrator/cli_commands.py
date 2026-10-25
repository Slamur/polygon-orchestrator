"""Реализация команд CLI: `init`, `llm generate|apply|status|accept`, `polygon push|status|pull`.

Парсер аргументов и входная точка — в `cli.py`; этот модуль он импортирует
только после разбора аргументов, потому что отсюда тянутся оба пайплайна (а
с ними pydantic и requests) — см. докстринг `cli.py`.
"""

from __future__ import annotations

import argparse
import logging
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
from orchestrator.polygon.pull import ITEM_ERROR as PULL_ITEM_ERROR
from orchestrator.polygon.pull import pull_problem
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.spec import SpecInitError, create_spec_draft
from orchestrator.step_order import STEP_ORDER

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


def _configure_console_logging(*, warnings_only: bool = False) -> logging.Logger:
    """Настраивает логгер пакета так, чтобы INFO-сообщения были видны в
    консоли — до этого `logger.info(...)` в `_log_usage` никуда не выводился,
    потому что для логгера не было ни уровня, ни handler'а.

    Пересоздаёт handler при каждом вызове (а не один раз при импорте модуля):
    `logging.StreamHandler()` фиксирует `sys.stdout` в момент создания, а
    `capsys` в тестах подменяет `sys.stdout` на время теста — handler,
    созданный при импорте, писал бы мимо этой подмены.

    `warnings_only` — для `polygon push`: в консоль идут только WARNING, а
    INFO (каждый HTTP-запрос, сообщения шагов) и ERROR (полный текст
    ошибки шага с traceback) — только в `polygon.log`; итог по каждому шагу
    печатает сам CLI, коротко (см. `_print_polygon_outcome`).
    """
    global _console_handler
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if _console_handler is not None:
        logger.removeHandler(_console_handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    if warnings_only:
        handler.addFilter(lambda record: record.levelno == logging.WARNING)
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
    handler.handle(
        logging.LogRecord(
            name=LOGGER_NAME,
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=_RUN_HEADER,
            args=(command_line,),
            exc_info=None,
        )
    )
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        handler.close()


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


def _print_pipeline_result(logger: logging.Logger, result) -> None:
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
        for note in outcome.notes:
            kind = note.get("kind", "?")
            field_name = note.get("field", "?")
            explanation = note.get("explanation", "")
            logger.info(f"      [{kind}] {field_name}: {explanation}")
        for mark, reason in outcome.removed:
            logger.info(f"      removed (review and delete by hand): {mark} — {reason}")
        for old_path, new_path, reason in outcome.renamed:
            logger.info(f"      renamed: {old_path} -> {new_path} — {reason}")
        if outcome.backup_dir is not None:
            logger.info(f"      previous versions of overwritten files: {outcome.backup_dir}")
        for filename, reason in outcome.conflicts:
            logger.info(f"      not applied: {filename} — {reason}")
        if outcome.conflicts_dir is not None:
            logger.info(f"      model versions of not applied files: {outcome.conflicts_dir}")

    if result.stopped_uncertain:
        logger.info(f"  ! pipeline stopped for '{result.problem_id}' — see the step above")


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
        _print_pipeline_result(logger, result)
    return 0 if result.ok else 1


def _print_step_statuses(problem_id: str, statuses, spec_error: str | None) -> bool:
    """Печатает `шаг: состояние — подробности` (или ошибку спека).
    Возвращает, была ли ошибка спека."""
    print(f"=== {problem_id} ===")
    if spec_error is not None:
        print(spec_error)
        return True

    for step_status in statuses:
        line = f"  {step_status.step_name}: {step_status.state}"
        if step_status.detail:
            line += f" — {step_status.detail}"
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


def _print_polygon_outcome(outcome, log_path: Path) -> None:
    """Печатает итог одного polygon-шага сразу по его завершении (`flush` —
    чтобы прогресс был виден и при выводе в pipe/файл). Ошибка — коротко,
    со ссылкой на `polygon.log`, где лежит полный текст."""
    line = f"  {outcome.step_name}: {outcome.status}"
    if outcome.status == POLYGON_OUTCOME_ERROR and outcome.full_detail:
        line += f" — {outcome.detail}"
    elif outcome.status == POLYGON_OUTCOME_ERROR:
        line += f" — {_shorten_error(outcome.detail)} (details: {log_path})"
    elif outcome.detail:
        line += f" — {outcome.detail}"
    print(line, flush=True)


def cmd_polygon_push(args: argparse.Namespace) -> int:
    logger = _configure_console_logging(warnings_only=True)
    log_path = Path(args.problems_dir) / args.problem_id / POLYGON_LOG

    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        result = run_polygon_pipeline(
            args.problem_id,
            only_step=args.step,
            force=args.force,
            problems_dir=args.problems_dir,
            on_outcome=lambda outcome: _print_polygon_outcome(outcome, log_path),
        )
    if result.spec_error is not None:
        print(result.spec_error, file=sys.stderr)
        return 1
    return 0 if result.ok else 1


def cmd_polygon_status(args: argparse.Namespace) -> int:
    _print_polygon_outcomes(
        compute_polygon_step_statuses(args.problem_id, problems_dir=args.problems_dir)
    )
    return 0


def cmd_polygon_pull(args: argparse.Namespace) -> int:
    """Привязка к существующей на Polygon задаче, сравнение с ней локальных
    файлов и выгрузка недостающих / с `--overwrite` — отличающихся (см.
    `orchestrator/polygon/pull.py`). Спек не нужен.
    Лог — в тот же `polygon.log`, что и у `polygon push`."""
    logger = _configure_console_logging(warnings_only=True)
    log_path = Path(args.problems_dir) / args.problem_id / POLYGON_LOG

    def print_item(item) -> None:
        line = f"  {item.name}: {item.status}"
        if item.status == PULL_ITEM_ERROR:
            line += f" — {_shorten_error(item.detail)} (details: {log_path})"
        elif item.detail:
            line += f" — {item.detail}"
        print(line, flush=True)

    with _problem_log_handler(
        logger, args.problems_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        try:
            result = pull_problem(
                args.problem_id,
                polygon_id=args.polygon_id,
                overwrite=args.overwrite,
                problems_dir=args.problems_dir,
                on_item=print_item,
            )
        except PolygonStepError as exc:
            logger.error("[pull] %s", exc)
            print(str(exc), file=sys.stderr)
            return 1
        except PolygonApiError as exc:
            logger.error("[pull] %s", exc)
            print(f"{_shorten_error(str(exc))} (details: {log_path})", file=sys.stderr)
            return 1
    print(f"  {result.link_detail}, revision {result.revision}")
    if result.backup_dir is not None:
        print(f"  overwritten local files are backed up in {result.backup_dir}")
    return 0 if result.ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    """Создаёт каталог задачи с черновиком `spec.yaml` (см.
    `spec.create_spec_draft`). Ни модель, ни Polygon не вызываются."""
    try:
        spec_path = create_spec_draft(
            args.problem_id, problems_dir=args.problems_dir, templates_dir=args.templates_dir
        )
    except SpecInitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"created {spec_path} — fill it in (format: docs/SPEC_FORMAT.md)")
    return 0
