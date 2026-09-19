"""Входная точка оркестратора: `run` и `status` (CLAUDE.md, "Пакетный запуск").

Сделано на стандартном `argparse`, а не на `click`/`typer`: в `pyproject.toml`
других CLI-фреймворков нет (только pydantic/PyYAML/Jinja2 — все три нужны
самому оркестратору, а не CLI), а команд у нас всего две с небольшим набором
флагов — `argparse` из стандартной библиотеки закрывает это без новой
зависимости. Если состав команд вырастет (подкоманды с подкомандами, shell-
автодополнение и т.п.), это решение стоит пересмотреть в пользу `click`.
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
    OUTPUTS_DIR,
    PROMPTS_DIR,
    SPECS_DIR,
    STEP_ORDER,
    TEMPLATES_DIR,
    compute_step_statuses,
    run_pipeline,
)

_OUTCOME_LABELS = {
    OUTCOME_CACHE_HIT: "cache hit",
    OUTCOME_CONFIRMED: "confirmed",
    OUTCOME_PROPOSED: "proposed (PROPOSED, REVIEW ME)",
    OUTCOME_UNCERTAIN: "uncertain",
    OUTCOME_SKIPPED_OPTIONAL: "skipped (optional)",
    "error": "error",
}

# Логгер пакета — на него вешается и консольный вывод (см.
# `_configure_console_logging`), и по-задачный `outputs/<id>/log.txt` (см.
# `_problem_log_handler`), см. CLAUDE.md, задача "лог выполнения". Дочерние
# логгеры (например, `orchestrator.llm_clients.anthropic_client`, откуда
# идёт `_log_usage`) пишут через propagate в этот же логгер — отдельно
# настраивать их не нужно.
LOGGER_NAME = "orchestrator"

_console_handler: logging.Handler | None = None


def _configure_console_logging() -> logging.Logger:
    """Настраивает логгер пакета так, чтобы INFO-сообщения были видны в
    консоли — до этого `logger.info(...)` в `_log_usage` никуда не выводился,
    потому что для логгера не было ни уровня, ни handler'а.

    Пересоздаёт handler при каждом вызове (а не один раз при импорте модуля):
    `logging.StreamHandler()` фиксирует `sys.stdout` в момент создания, а
    `capsys` в тестах подменяет `sys.stdout` на время теста — handler,
    созданный при импорте, писал бы мимо этой подмены.
    """
    global _console_handler
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if _console_handler is not None:
        logger.removeHandler(_console_handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _console_handler = handler
    return logger


@contextmanager
def _problem_log_handler(
    logger: logging.Logger, outputs_dir: Path, problem_id: str
) -> Iterator[logging.Handler]:
    """Заводит `outputs/<problem_id>/log.txt` на время обработки одного
    `problem_id` — открывается на перезапись (см. CLAUDE.md, задача "лог
    выполнения": "при повторном запуске лог должен отражать последний
    прогон, а не накапливаться"), добавляется к логгеру перед прогоном шагов
    и снимается сразу после, через try/finally — чтобы в `run --all` лог
    одной задачи не утёк в файл следующей, и чтобы необработанное исключение
    всё равно не оставило handler висящим.

    Отдаёт сам handler — вызывающий код использует его напрямую в
    `_log_traceback_to_file` для необработанных исключений (см. там же,
    почему это не идёт через обычный `logger.exception`).
    """
    problem_dir = Path(outputs_dir) / problem_id
    problem_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(problem_dir / "log.txt", mode="w", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    старый `except Exception` в `run --all`, без traceback вообще), а
    `outputs/<id>/log.txt` при этом не терял traceback (CLAUDE.md, задача
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
        logger.info(f"  ! пайплайн остановлен для '{result.problem_id}' — см. шаг выше")


def _cmd_run(args: argparse.Namespace) -> int:
    logger = _configure_console_logging()

    if args.all:
        problem_ids = _discover_problem_ids(args.specs_dir)
        if not problem_ids:
            logger.info(f"в {args.specs_dir} не найдено ни одного specs/*.yaml")
            return 0

        any_failed = False
        for problem_id in problem_ids:
            with _problem_log_handler(logger, args.outputs_dir, problem_id) as file_handler:
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
                    logger.info(f"  ! неожиданная ошибка: {exc}")
                    _log_traceback_to_file(
                        file_handler, f"необработанная ошибка при обработке '{problem_id}'"
                    )
                    any_failed = True
                    continue

                _print_pipeline_result(logger, result)
                if not result.ok:
                    any_failed = True
        return 1 if any_failed else 0

    if args.step is not None and args.step not in STEP_ORDER:
        print(f"неизвестный шаг '{args.step}', допустимые: {', '.join(STEP_ORDER)}", file=sys.stderr)
        return 2

    with _problem_log_handler(logger, args.outputs_dir, args.problem_id) as file_handler:
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
                file_handler, f"необработанная ошибка при обработке '{args.problem_id}'"
            )
            raise
        _print_pipeline_result(logger, result)
    return 0 if result.ok else 1


def _cmd_status(args: argparse.Namespace) -> int:
    problem_ids = (
        [args.problem] if args.problem is not None else _discover_problem_ids(args.specs_dir)
    )
    if not problem_ids:
        print(f"в {args.specs_dir} не найдено ни одного specs/*.yaml")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Прогон генеративных шагов подготовки задачи для Polygon (см. CLAUDE.md)",
    )
    parser.add_argument(
        "--specs-dir", type=Path, default=SPECS_DIR, help="каталог specs/*.yaml (по умолчанию specs/)"
    )
    parser.add_argument(
        "--prompts-dir", type=Path, default=PROMPTS_DIR, help="каталог prompts/ (по умолчанию prompts/)"
    )
    parser.add_argument(
        "--outputs-dir", type=Path, default=OUTPUTS_DIR, help="каталог outputs/ (по умолчанию outputs/)"
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=TEMPLATES_DIR,
        help="каталог templates/ (по умолчанию templates/)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="прогнать шаги для одной задачи или для всех specs/*.yaml")
    run_group = run_parser.add_mutually_exclusive_group(required=True)
    run_group.add_argument("problem_id", nargs="?", help="problem_id (соответствует specs/<problem_id>.yaml)")
    run_group.add_argument("--all", action="store_true", help="прогнать все specs/*.yaml")
    run_parser.add_argument(
        "--step",
        choices=STEP_ORDER,
        default=None,
        help="прогнать только один шаг (несовместимо с --all)",
    )
    run_parser.add_argument(
        "--force", action="store_true", help="игнорировать кэш и вызвать модель заново"
    )
    run_parser.set_defaults(func=_cmd_run)

    status_parser = subparsers.add_parser(
        "status", help="таблица шаг -> cache hit / stale / not run / uncertain, без вызова модели"
    )
    status_parser.add_argument("--problem", default=None, help="ограничиться одним problem_id")
    status_parser.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run" and args.all and args.step is not None:
        parser.error("--step несовместим с --all")

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
