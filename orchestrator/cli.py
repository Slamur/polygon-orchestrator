"""Входная точка оркестратора. Все команды имеют вид
`orchestrator <адрес задачи> <pipeline> <command> [аргументы]` (CLAUDE.md,
"Запуск"): `init` — каталог задачи с черновиком спека (из шаблона, по
спеку задачи-основы `--ref` и/или моделью по черновику автора `--draft`),
`llm generate|apply|status|accept` — генеративные шаги,
`local check|fix` — проверка файлов задачи и механическое исправление
найденного в условии, без модели и без сети, `polygon push|status|pull` —
загрузка задачи в Polygon и выгрузка из него. Группы разделены по тому, чего
стоит команда: `llm` вызывает модель, `polygon` ходит в сеть, `local` — ни то,
ни другое.
Адрес задачи — путь её каталога с корнем (`problems/contest/2026/round-1/my-task`,
`examples/array/prefix-sums`, см. `orchestrator/problem_paths.py`) — идёт
первым: обычно работа идёт над одной задачей, и разные команды к ней удобнее
менять в конце строки.

Здесь только парсер аргументов и диспетчеризация; сами команды — в
`cli_commands.py`, и импортируется он лениво, уже после разбора аргументов.
Причина — Tab-дополнение (`argcomplete`, настройка shell — в `docs/SETUP.md`):
на каждое нажатие Tab shell запускает этот модуль целиком, чтобы получить
кандидатов, и импорт пайплайнов (pydantic, requests) делал бы каждое нажатие
заметно медленным. Поэтому на верхнем уровне этого модуля не должно быть
импортов тяжелее `cache`/`step_order`/`problem_paths`/`drafts`.

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

from orchestrator.cache import PROMPTS_DIR, TEMPLATES_DIR
from orchestrator.drafts import DRAFTS_DIR, draft_names
from orchestrator.problem_paths import (
    PROBLEM_ROOTS,
    ProblemPathError,
    migrate_to_authoring,
    parse_problem_address,
    problem_address,
    problem_paths,
)
from orchestrator.step_order import POLYGON_STEP_ORDER, STEP_ORDER


def _complete_problem_addresses(
    prefix: str, parsed_args: argparse.Namespace, **kwargs
) -> list[str]:
    """Кандидаты Tab-дополнения адреса задачи: адреса всех задач в
    `problems/` и `examples/` — со спеком (`spec.yaml`) или без него, только
    с `polygon_state.json` (`polygon status|pull` спек не нужен, а команда
    набирается после адреса и на момент дополнения ещё неизвестна).

    Список не хранится нигде, а читается из каталогов при каждом нажатии Tab —
    с учётом `--base-dir`, если он уже набран в командной строке.
    Фильтрацию по набранному префиксу делает сам `argcomplete`.
    """
    base_dir = Path(getattr(parsed_args, "base_dir", None) or ".")
    return [
        problem_address(root, path)
        for root in PROBLEM_ROOTS
        for path in problem_paths(base_dir / root)
    ]


def _complete_draft_names(prefix: str, parsed_args: argparse.Namespace, **kwargs) -> list[str]:
    """Кандидаты Tab-дополнения `init --draft`: пути файлов относительно
    `drafts/` на любой глубине (с учётом `--base-dir`), читаются при каждом
    нажатии Tab."""
    base_dir = Path(getattr(parsed_args, "base_dir", None) or ".")
    return draft_names(base_dir / DRAFTS_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Run generative problem-preparation steps for Polygon (see CLAUDE.md)",
    )
    parser.add_argument(
        "--prompts-dir", type=Path, default=PROMPTS_DIR, help="prompts/ directory (default: prompts/)"
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("."),
        help="directory containing problems/ and examples/ (default: current directory)",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=TEMPLATES_DIR,
        help="templates/ directory (default: templates/)",
    )

    parser.add_argument(
        "problem",
        metavar="PROBLEM",
        help="problem directory with its root: problems/<path> or examples/<path>, "
        "e.g. problems/contest/my-task (spec: <PROBLEM>/spec.yaml)",
    ).completer = _complete_problem_addresses

    pipelines = parser.add_subparsers(dest="pipeline", required=True)

    init_parser = pipelines.add_parser(
        "init",
        help="create the problem directory with a draft spec.yaml: from "
        "templates/spec_template.yaml, from the spec of another problem (--ref) and/or "
        "written by the model from the author's draft (--draft, calls the model); "
        "an existing spec is revised by the model only with --draft, when the draft changed",
    )
    init_parser.add_argument(
        "--draft",
        metavar="NAME",
        default=None,
        help="free-form description of the problem in drafts/ (file name with or without "
        "extension): the model writes the spec from it (calls the model), the draft is "
        "copied into the problem directory; if the spec exists, the model revises it by "
        "what changed in the draft since its copy",
    ).completer = _complete_draft_names
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="with --draft and an existing spec: revise it even if the draft has not changed",
    )
    init_parser.add_argument(
        "--ref",
        metavar="PROBLEM",
        default=None,
        help="base the spec on the spec of this problem (problems/<path> or "
        "examples/<path>) and add it to the spec references; without --draft the spec "
        "is copied with the new problem_id",
    ).completer = _complete_problem_addresses
    init_parser.set_defaults(handler="cmd_init")

    llm_parser = pipelines.add_parser("llm", help="generative steps (calls the model via API)")
    llm_commands = llm_parser.add_subparsers(dest="command", required=True)

    generate_parser = llm_commands.add_parser("generate", help="run generative steps")
    generate_parser.add_argument(
        "--step", choices=STEP_ORDER, default=None, help="run only one step"
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="ignore the cache and call the model again (revising the current files)",
    )
    generate_parser.add_argument(
        "--fresh",
        action="store_true",
        help="generate from scratch without showing the current files to the model "
        "(implies --force)",
    )
    generate_parser.set_defaults(handler="cmd_llm_generate")

    apply_parser = llm_commands.add_parser(
        "apply",
        help="apply model responses saved by a failed 'llm generate' (the response did not pass "
        "the checks) without calling the model",
    )
    apply_parser.add_argument(
        "--step", choices=STEP_ORDER, default=None, help="apply only one step"
    )
    apply_parser.set_defaults(handler="cmd_llm_apply")

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

    local_parser = pipelines.add_parser(
        "local", help="local checks and fixes of the problem files (no model, no network)"
    )
    local_commands = local_parser.add_subparsers(dest="command", required=True)

    local_check_parser = local_commands.add_parser(
        "check",
        help="check the problem files: Markdown lists and quotes other than <<...>> "
        "in statement/*.tex; identical lines in the expanded FreeMarker test script "
        "(Polygon refuses to generate tests from them) and the script lines printing them "
        "(the script check needs java, downloads FreeMarker once)",
    )
    local_check_parser.add_argument(
        "--full",
        action="store_true",
        help="also generate the tests from the script locally (cached in .tests_cache/, only "
        "new tests are generated), run the validator on them and look for equal tests",
    )
    local_check_parser.set_defaults(handler="cmd_local_check")

    local_run_parser = local_commands.add_parser(
        "run",
        help="build the validator and all solutions and run them on the examples: each "
        "solution must get the verdict its name allows (X: OK or X), the problem checker "
        "judges the output, TL from limits.yaml (local time)",
    )
    local_run_parser.add_argument(
        "--full",
        action="store_true",
        help="also generate the tests from the script locally (see 'local check --full') and "
        "run the solutions on them: 'wa', 're', 'il' must get their verdict at least once and "
        "stop at the first one, expected outputs come from the main ok solution",
    )
    local_run_parser.set_defaults(handler="cmd_local_run")

    local_fix_parser = local_commands.add_parser(
        "fix",
        help="fix statement/*.tex issues found by 'local check': Markdown lists -> "
        "itemize/enumerate, quotes -> <<...>> (old versions go to <PROBLEM>/.fix_backup/)",
    )
    local_fix_parser.set_defaults(handler="cmd_local_fix")

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
    polygon_push_parser.add_argument(
        "--no-commit",
        action="store_true",
        help="leave the uploads uncommitted in the Polygon working copy: commit them on Polygon "
        "yourself, then run `polygon pull`",
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
        help="numeric Polygon problem id (default: search Polygon by the problem_id as the name)",
    )
    polygon_pull_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="also replace local files changed both locally and on Polygon since the last "
        "sync, or with no sync record (files changed only on Polygon are replaced without "
        "it; old versions go to <PROBLEM>/.pull_backup/)",
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
    # Опции — только когда набран "-": иначе Tab на месте адреса задачи
    # показывал бы флаги вперемешку с именами задач.
    argcomplete.autocomplete(parser, always_complete_options=False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    _autocomplete(parser)
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)
    args.command_line = " ".join(argv)
    # Дальше задача адресуется парой (корень, путь внутри корня) — см.
    # `orchestrator/problem_paths.py`.
    try:
        root, args.problem_id = parse_problem_address(args.problem)
    except ProblemPathError as exc:
        parser.error(str(exc))
    args.problems_dir = args.base_dir / root
    # Задача, созданная до `authoring/`, переносится при первой же команде
    # к ней — дальше все команды видят только новую раскладку.
    problem_dir = args.problems_dir / args.problem_id
    try:
        moves = migrate_to_authoring(problem_dir)
    except ProblemPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for source, target in moves:
        print(f"moved {source} -> {target}", file=sys.stderr)

    from orchestrator import cli_commands

    return getattr(cli_commands, args.handler)(args)


if __name__ == "__main__":
    sys.exit(main())
