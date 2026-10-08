"""Реализация команд CLI: `llm generate|status`, `polygon push|status|pull`.

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
    OUTCOME_PROPOSED,
    OUTCOME_SKIPPED_OPTIONAL,
    OUTCOME_UNCERTAIN,
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
from orchestrator.step_order import STEP_ORDER

_OUTCOME_LABELS = {
    OUTCOME_CACHE_HIT: "cache hit",
    OUTCOME_CONFIRMED: "confirmed",
    OUTCOME_PROPOSED: "proposed (PROPOSED, REVIEW ME)",
    OUTCOME_UNCERTAIN: "uncertain",
    OUTCOME_SKIPPED_OPTIONAL: "skipped (optional)",
    "error": "error",
}

# Логгер пакета — на него вешается и консольный вывод (см.
# `_configure_console_logging`), и по-задачные лог-файлы
# `outputs/<id>/llm_generation.log` / `outputs/<id>/polygon.log` (см.
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
# целиком он пишется в `polygon.log`.
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
    outputs_dir: Path,
    problem_id: str,
    log_name: str,
    command_line: str,
) -> Iterator[logging.Handler]:
    """Заводит `outputs/<problem_id>/<log_name>` на время обработки одного
    `problem_id` — открывается на дозапись: лог накапливает историю
    прогонов, каждый прогон начинается с заголовка с командой (см.
    `_RUN_HEADER`) — по нему удобно искать нужный запуск, а время в каждой
    строке показывает длительность шагов. Handler добавляется к логгеру
    перед прогоном шагов и снимается сразу после, через try/finally — чтобы
    в `llm generate --all` лог одной задачи не утёк в файл следующей, и чтобы
    необработанное исключение всё равно не оставило handler висящим.

    Отдаёт сам handler — вызывающий код использует его напрямую в
    `_log_traceback_to_file` для необработанных исключений (см. там же,
    почему это не идёт через обычный `logger.exception`).
    """
    problem_dir = Path(outputs_dir) / problem_id
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
    туда и так печатает сам Python при завершении процесса / его печатал
    старый `except Exception` в `llm generate --all`, без traceback вообще), а
    `outputs/<id>/llm_generation.log` при этом не терял traceback (CLAUDE.md, задача
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


def _discover_problem_ids(specs_dir: Path) -> list[str]:
    return sorted(path.stem for path in Path(specs_dir).glob("*.yaml"))


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

    if result.stopped_uncertain:
        logger.info(f"  ! pipeline stopped for '{result.problem_id}' — see the step above")


def cmd_llm_generate(args: argparse.Namespace) -> int:
    logger = _configure_console_logging()

    if args.all:
        problem_ids = _discover_problem_ids(args.specs_dir)
        if not problem_ids:
            logger.info(f"no specs/*.yaml found in {args.specs_dir}")
            return 0

        any_failed = False
        for problem_id in problem_ids:
            with _problem_log_handler(
                logger, args.outputs_dir, problem_id, LLM_GENERATION_LOG, args.command_line
            ) as file_handler:
                try:
                    result = run_pipeline(
                        problem_id,
                        force=args.force,
                        specs_dir=args.specs_dir,
                        prompts_dir=args.prompts_dir,
                        outputs_dir=args.outputs_dir,
                        templates_dir=args.templates_dir,
                    )
                except Exception as exc:  # noqa: BLE001 — один problem_id не должен ронять весь --all
                    logger.info(f"=== {problem_id} ===")
                    logger.info(f"  ! unexpected error: {exc}")
                    _log_traceback_to_file(
                        file_handler, f"unhandled error while processing '{problem_id}'"
                    )
                    any_failed = True
                    continue

                _print_pipeline_result(logger, result)
                if not result.ok:
                    any_failed = True
        return 1 if any_failed else 0

    if args.step is not None and args.step not in STEP_ORDER:
        print(f"unknown step '{args.step}', allowed: {', '.join(STEP_ORDER)}", file=sys.stderr)
        return 2

    with _problem_log_handler(
        logger, args.outputs_dir, args.problem_id, LLM_GENERATION_LOG, args.command_line
    ) as file_handler:
        try:
            result = run_pipeline(
                args.problem_id,
                force=args.force,
                only_step=args.step,
                specs_dir=args.specs_dir,
                prompts_dir=args.prompts_dir,
                outputs_dir=args.outputs_dir,
                templates_dir=args.templates_dir,
            )
        except Exception:
            _log_traceback_to_file(
                file_handler, f"unhandled error while processing '{args.problem_id}'"
            )
            raise
        _print_pipeline_result(logger, result)
    return 0 if result.ok else 1


def cmd_llm_status(args: argparse.Namespace) -> int:
    problem_ids = (
        [args.problem_id]
        if args.problem_id is not None
        else _discover_problem_ids(args.specs_dir)
    )
    if not problem_ids:
        print(f"no specs/*.yaml found in {args.specs_dir}")
        return 0

    any_error = False
    for problem_id in problem_ids:
        print(f"=== {problem_id} ===")
        statuses, spec_error = compute_step_statuses(
            problem_id,
            specs_dir=args.specs_dir,
            prompts_dir=args.prompts_dir,
            outputs_dir=args.outputs_dir,
            templates_dir=args.templates_dir,
        )
        if spec_error is not None:
            print(spec_error)
            any_error = True
            continue

        for step_status in statuses:
            line = f"  {step_status.step_name}: {step_status.state}"
            if step_status.detail:
                line += f" — {step_status.detail}"
            print(line)

    return 1 if any_error else 0


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
    if outcome.status == POLYGON_OUTCOME_ERROR:
        line += f" — {_shorten_error(outcome.detail)} (details: {log_path})"
    elif outcome.detail:
        line += f" — {outcome.detail}"
    print(line, flush=True)


def cmd_polygon_push(args: argparse.Namespace) -> int:
    logger = _configure_console_logging(warnings_only=True)
    log_path = Path(args.outputs_dir) / args.problem_id / POLYGON_LOG

    with _problem_log_handler(
        logger, args.outputs_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        result = run_polygon_pipeline(
            args.problem_id,
            only_step=args.step,
            specs_dir=args.specs_dir,
            outputs_dir=args.outputs_dir,
            on_outcome=lambda outcome: _print_polygon_outcome(outcome, log_path),
        )
    if result.spec_error is not None:
        print(result.spec_error, file=sys.stderr)
        return 1
    return 0 if result.ok else 1


def cmd_polygon_status(args: argparse.Namespace) -> int:
    _print_polygon_outcomes(
        compute_polygon_step_statuses(args.problem_id, outputs_dir=args.outputs_dir)
    )
    return 0


def cmd_polygon_pull(args: argparse.Namespace) -> int:
    """Привязка к существующей на Polygon задаче и выгрузка недостающих
    локально файлов (см. `orchestrator/polygon/pull.py`). Спек не нужен.
    Лог — в тот же `polygon.log`, что и у `polygon push`."""
    logger = _configure_console_logging(warnings_only=True)
    log_path = Path(args.outputs_dir) / args.problem_id / POLYGON_LOG

    def print_item(item) -> None:
        line = f"  {item.name}: {item.status}"
        if item.status == PULL_ITEM_ERROR:
            line += f" — {_shorten_error(item.detail)} (details: {log_path})"
        elif item.detail:
            line += f" — {item.detail}"
        print(line, flush=True)

    with _problem_log_handler(
        logger, args.outputs_dir, args.problem_id, POLYGON_LOG, args.command_line
    ):
        try:
            result = pull_problem(
                args.problem_id,
                polygon_id=args.polygon_id,
                outputs_dir=args.outputs_dir,
                on_item=print_item,
            )
        except (PolygonApiError, PolygonStepError) as exc:
            logger.error("[pull] %s", exc)
            print(f"{_shorten_error(str(exc))} (details: {log_path})", file=sys.stderr)
            return 1
    print(f"  {result.link_detail}")
    return 0 if result.ok else 1
