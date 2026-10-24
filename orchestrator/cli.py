"""Входная точка оркестратора. Все команды имеют вид
`orchestrator <problem_id> <pipeline> <command> [аргументы]` (CLAUDE.md,
"Запуск"): `init` — каталог задачи с черновиком спека,
`llm generate|status|accept` — генеративные шаги,
`polygon push|status|pull` — загрузка задачи в Polygon и выгрузка из него.
`problem_id` идёт первым: обычно работа идёт над одной задачей, и разные
команды к ней удобнее менять в конце строки.

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


def _complete_problem_ids(prefix: str, parsed_args: argparse.Namespace, **kwargs) -> list[str]:
    """Кандидаты Tab-дополнения `problem_id`: спеки и каталоги `outputs/`.
    Команда набирается после `problem_id` и на момент дополнения ещё
    неизвестна, а `polygon status|pull` работают и с задачей, у которой есть
    только каталог в `outputs/`.

    Список не хранится нигде, а читается из каталогов при каждом нажатии Tab —
    с учётом `--specs-dir`/`--outputs-dir`, если они уже набраны в командной
    строке. Фильтрацию по набранному префиксу делает сам `argcomplete`.
    """
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

    parser.add_argument(
        "problem_id", help="matches specs/<problem_id>.yaml and outputs/<problem_id>/"
    ).completer = _complete_problem_ids

    pipelines = parser.add_subparsers(dest="pipeline", required=True)

    init_parser = pipelines.add_parser(
        "init",
        help="create outputs/<problem_id>/ with a draft spec.yaml from "
        "templates/common/spec_template.yaml (never overwrites an existing spec)",
    )
    init_parser.set_defaults(handler="cmd_init")

    llm_parser = pipelines.add_parser("llm", help="generative steps (calls the model via API)")
    llm_commands = llm_parser.add_subparsers(dest="command", required=True)

    generate_parser = llm_commands.add_parser("generate", help="run generative steps")
    generate_parser.add_argument(
        "--step", choices=STEP_ORDER, default=None, help="run only one step"
    )
    generate_parser.add_argument(
        "--force", action="store_true", help="ignore the cache and call the model again"
    )
    generate_parser.set_defaults(handler="cmd_llm_generate")

    llm_status_parser = llm_commands.add_parser(
        "status", help="table step -> cache hit / stale / not run / uncertain, without calling the model"
    )
    llm_status_parser.set_defaults(handler="cmd_llm_status")

    accept_parser = llm_commands.add_parser(
        "accept",
        help="mark current outputs of stale steps as up to date (rewrite their cache with current "
        "hashes) without calling the model",
    )
    accept_parser.add_argument(
        "--step", choices=STEP_ORDER, default=None, help="accept only one step"
    )
    accept_parser.set_defaults(handler="cmd_llm_accept")

    polygon_parser = pipelines.add_parser(
        "polygon", help="upload the problem to Polygon / download it (API polygon.codeforces.com)"
    )
    polygon_commands = polygon_parser.add_subparsers(dest="command", required=True)

    polygon_push_parser = polygon_commands.add_parser(
        "push",
        help="upload to Polygon: run polygon steps in order, uploading only what changed "
        "since the last sync (stops if the problem was changed on Polygon)",
    )
    polygon_push_parser.add_argument(
        "--step", choices=POLYGON_STEP_ORDER, default=None, help="run only one polygon step"
    )
    polygon_push_parser.add_argument(
        "--force",
        action="store_true",
        help="upload everything, even if unchanged or if the problem was changed on Polygon",
    )
    polygon_push_parser.set_defaults(handler="cmd_polygon_push")

    polygon_status_parser = polygon_commands.add_parser(
        "status", help="which polygon steps are already done, without network access"
    )
    polygon_status_parser.set_defaults(handler="cmd_polygon_status")

    polygon_pull_parser = polygon_commands.add_parser(
        "pull",
        help="link to an existing Polygon problem, compare local files with it and download "
        "the missing ones (no spec needed)",
    )
    polygon_pull_parser.add_argument(
        "--polygon-id",
        type=int,
        default=None,
        help="numeric Polygon problem id (default: search Polygon by problem_id as the name)",
    )
    polygon_pull_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace local files that differ from Polygon (old versions go to "
        "outputs/<problem_id>/.pull_backup/)",
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

    from orchestrator import cli_commands

    return getattr(cli_commands, args.handler)(args)


if __name__ == "__main__":
    sys.exit(main())
