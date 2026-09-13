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
import sys
from pathlib import Path

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


def _discover_problem_ids(specs_dir: Path) -> list[str]:
    return sorted(path.stem for path in Path(specs_dir).glob("*.yaml"))


def _print_pipeline_result(result) -> None:
    print(f"=== {result.problem_id} ===")
    if result.spec_error is not None:
        print(result.spec_error)
        return

    for outcome in result.outcomes:
        label = _OUTCOME_LABELS.get(outcome.status, outcome.status)
        line = f"  {outcome.step_name}: {label}"
        if outcome.detail:
            line += f" — {outcome.detail}"
        print(line)
        for note in outcome.notes:
            kind = note.get("kind", "?")
            field_name = note.get("field", "?")
            explanation = note.get("explanation", "")
            print(f"      [{kind}] {field_name}: {explanation}")

    if result.stopped_uncertain:
        print(f"  ! пайплайн остановлен для '{result.problem_id}' — см. шаг выше")


def _cmd_run(args: argparse.Namespace) -> int:
    if args.all:
        problem_ids = _discover_problem_ids(args.specs_dir)
        if not problem_ids:
            print(f"в {args.specs_dir} не найдено ни одного specs/*.yaml")
            return 0

        any_failed = False
        for problem_id in problem_ids:
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
                print(f"=== {problem_id} ===")
                print(f"  ! неожиданная ошибка: {exc}")
                any_failed = True
                continue

            _print_pipeline_result(result)
            if not result.ok:
                any_failed = True
        return 1 if any_failed else 0

    if args.step is not None and args.step not in STEP_ORDER:
        print(f"неизвестный шаг '{args.step}', допустимые: {', '.join(STEP_ORDER)}", file=sys.stderr)
        return 2

    result = run_pipeline(
        args.problem_id,
        force=args.force,
        only_step=args.step,
        specs_dir=args.specs_dir,
        prompts_dir=args.prompts_dir,
        outputs_dir=args.outputs_dir,
        templates_dir=args.templates_dir,
    )
    _print_pipeline_result(result)
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
