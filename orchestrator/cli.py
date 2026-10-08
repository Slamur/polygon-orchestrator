"""Входная точка оркестратора. Все команды имеют вид
`orchestrator <pipeline> <command> <problem_id> [аргументы]` (CLAUDE.md,
"Пакетный запуск"): `llm generate|status` — генеративные шаги,
`polygon push|status|pull` — загрузка задачи в Polygon и выгрузка из него.

Здесь только парсер аргументов и диспетчеризация; сами команды — в
`cli_commands.py`, и импортируется он лениво, уже после разбора аргументов.
Причина — Tab-дополнение (`argcomplete`, настройка shell — в `docs/SETUP.md`):
на каждое нажатие Tab shell запускает этот модуль целиком, чтобы получить
кандидатов, и импорт пайплайнов (pydantic, requests) делал бы каждое нажатие
заметно медленным. Поэтому на верхнем уровне этого модуля не должно быть
импортов тяжелее `cache`/`step_order`.

Сделано на стандартном `argparse`, а не на `click`/`typer`: команд у нас
немного, с небольшим набором флагов, и `argparse` из стандартной библиотеки
закрывает это без CLI-фреймворка в зависимостях; автодополнение к нему
добавляет `argcomplete`, не меняя сам парсер.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from orchestrator.cache import OUTPUTS_DIR, PROMPTS_DIR, SPECS_DIR, TEMPLATES_DIR
from orchestrator.step_order import POLYGON_STEP_ORDER, STEP_ORDER


def _spec_ids(parsed_args: argparse.Namespace) -> set[str]:
    specs_dir = getattr(parsed_args, "specs_dir", None) or SPECS_DIR
    return {path.stem for path in Path(specs_dir).glob("*.yaml")}


def _output_ids(parsed_args: argparse.Namespace) -> set[str]:
    outputs_dir = Path(getattr(parsed_args, "outputs_dir", None) or OUTPUTS_DIR)
    if not outputs_dir.is_dir():
        return set()
    return {
        path.name
        for path in outputs_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }


def _complete_spec_ids(prefix: str, parsed_args: argparse.Namespace, **kwargs) -> list[str]:
    """Кандидаты Tab-дополнения `problem_id` для команд, которым нужен спек.

    Список не хранится нигде, а читается из каталога при каждом нажатии Tab —
    с учётом `--specs-dir`, если он уже набран в командной строке. Фильтрацию
    по набранному префиксу делает сам `argcomplete`.
    """
    return sorted(_spec_ids(parsed_args))


def _complete_spec_and_output_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs
) -> list[str]:
    """То же для команд, работающих и без спека (`polygon status|pull`):
    задача может существовать только как каталог в `outputs/`."""
    return sorted(_spec_ids(parsed_args) | _output_ids(parsed_args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Run generative problem-preparation steps for Polygon (see CLAUDE.md)",
    )
    parser.add_argument(
        "--specs-dir", type=Path, default=SPECS_DIR, help="specs/*.yaml directory (default: specs/)"
    )
    parser.add_argument(
        "--prompts-dir", type=Path, default=PROMPTS_DIR, help="prompts/ directory (default: prompts/)"
    )
    parser.add_argument(
        "--outputs-dir", type=Path, default=OUTPUTS_DIR, help="outputs/ directory (default: outputs/)"
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=TEMPLATES_DIR,
        help="templates/ directory (default: templates/)",
    )

    pipelines = parser.add_subparsers(dest="pipeline", required=True)

    llm_parser = pipelines.add_parser("llm", help="generative steps (calls the model via API)")
    llm_commands = llm_parser.add_subparsers(dest="command", required=True)

    generate_parser = llm_commands.add_parser(
        "generate", help="run generative steps for one problem or for all specs/*.yaml"
    )
    generate_group = generate_parser.add_mutually_exclusive_group(required=True)
    generate_group.add_argument(
        "problem_id", nargs="?", help="problem_id (matches specs/<problem_id>.yaml)"
    ).completer = _complete_spec_ids
    generate_group.add_argument("--all", action="store_true", help="run all specs/*.yaml")
    generate_parser.add_argument(
        "--step",
        choices=STEP_ORDER,
        default=None,
        help="run only one step (incompatible with --all)",
    )
    generate_parser.add_argument(
        "--force", action="store_true", help="ignore the cache and call the model again"
    )
    generate_parser.set_defaults(handler="cmd_llm_generate")

    llm_status_parser = llm_commands.add_parser(
        "status", help="table step -> cache hit / stale / not run / uncertain, without calling the model"
    )
    llm_status_parser.add_argument(
        "problem_id",
        nargs="?",
        default=None,
        help="limit to one problem_id (default: all specs/*.yaml)",
    ).completer = _complete_spec_ids
    llm_status_parser.set_defaults(handler="cmd_llm_status")

    polygon_parser = pipelines.add_parser(
        "polygon", help="upload the problem to Polygon / download it (API polygon.codeforces.com)"
    )
    polygon_commands = polygon_parser.add_subparsers(dest="command", required=True)
    problem_id_help = "matches specs/<problem_id>.yaml and outputs/<problem_id>/"

    # Без --force: кэша у polygon-шагов нет, обходить нечего.
    polygon_push_parser = polygon_commands.add_parser(
        "push", help="upload to Polygon: run polygon steps in order (no cache — steps are invoked every time)"
    )
    polygon_push_parser.add_argument(
        "problem_id", help=problem_id_help
    ).completer = _complete_spec_ids
    polygon_push_parser.add_argument(
        "--step", choices=POLYGON_STEP_ORDER, default=None, help="run only one polygon step"
    )
    polygon_push_parser.set_defaults(handler="cmd_polygon_push")

    polygon_status_parser = polygon_commands.add_parser(
        "status", help="which polygon steps are already done, without network access"
    )
    polygon_status_parser.add_argument(
        "problem_id", help=problem_id_help
    ).completer = _complete_spec_and_output_ids
    polygon_status_parser.set_defaults(handler="cmd_polygon_status")

    polygon_pull_parser = polygon_commands.add_parser(
        "pull",
        help="link to an existing Polygon problem and download the parts missing locally "
        "(existing local files are never overwritten; no spec needed)",
    )
    polygon_pull_parser.add_argument(
        "problem_id", help=problem_id_help
    ).completer = _complete_spec_and_output_ids
    polygon_pull_parser.add_argument(
        "--polygon-id",
        type=int,
        default=None,
        help="numeric Polygon problem id (default: search Polygon by problem_id as the name)",
    )
    polygon_pull_parser.set_defaults(handler="cmd_polygon_pull")

    return parser


def _autocomplete(parser: argparse.ArgumentParser) -> None:
    """В режиме Tab-дополнения (shell выставляет `_ARGCOMPLETE`) печатает
    кандидатов и завершает процесс; при обычном запуске ничего не делает.

    Без установленного `argcomplete` CLI работает как раньше, просто без
    дополнения — на случай окружения, где зависимости не переустановили
    после обновления кода.
    """
    if "_ARGCOMPLETE" not in os.environ:
        return
    try:
        import argcomplete
    except ImportError:
        return
    # Опции — только когда набран "-": иначе Tab на месте problem_id
    # показывал бы флаги вперемешку с именами задач.
    argcomplete.autocomplete(parser, always_complete_options=False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    _autocomplete(parser)
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)
    args.command_line = " ".join(argv)

    if args.handler == "cmd_llm_generate" and args.all and args.step is not None:
        parser.error("--step is incompatible with --all")

    from orchestrator import cli_commands

    return getattr(cli_commands, args.handler)(args)


if __name__ == "__main__":
    sys.exit(main())
