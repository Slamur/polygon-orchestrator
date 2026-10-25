"""Polygon-шаг 9 — загрузка всех файлов из `problems/<problem_id>/solutions/`
(результат генеративного шага `solutions_draft`) в Polygon
(`problem.saveSolution`) с тегом вердикта для каждого.

Тег выводится из имени файла по схеме
`<verdict>_<language>_<description>_<author>.<ext>`
(`prompts/solutions_draft/system.md`, правило 5):

- ровно один `ok`-файл получает `MA` (Main correct — эталон для генерации
  ответов на тесты, `templates/tutorials/solutions.md`, "Решения в Polygon"),
  выбор — по приоритету языков `_MAIN_LANGUAGE_PRIORITY`, внутри языка — по
  алфавиту имён;
- остальные `ok` -> `OK`, `tl`/`tle` -> `TL`, `tl_ok` -> `TO` ("TL или OK"),
  `wa` -> `WA`, `ml`/`mle` -> `ML`;
- всё прочее допустимое (`re`) -> `RJ` ("Incorrect").

Риск 1: `Solutions.languages` в спеке — `list[str]` без ограничения
значений, и модель может записать язык в имени файла по-разному (`py`,
`python`, `python3`, `c++`). Известные варианты приводятся к одному имени
через `_LANGUAGE_ALIASES`; если язык всё равно не из приоритета, шаг не
падает, а пишет WARNING и берёт Main correct первым по алфавиту
`ok`-файлом — автору стоит проверить выбор в UI.

Риск 2: `sourceType` не передаётся — не подтверждено, какое значение
ожидает Polygon для каждого языка; предполагается, что он определит язык по
расширению, как в `upload_validator`/`upload_generators`. Первый реальный
вызов обязательно сверить в UI.

Все имена файлов разбираются и Main correct выбирается до первого сетевого
вызова: файл с неподходящим именем — `PolygonStepError`, а не тихий пропуск.

Загружаются только решения, чьё содержимое отличается от базы
синхронизации; тег в базу не входит и отправляется только вместе с
загружаемым файлом. Поэтому тег, поправленный в UI Polygon (например, `TO`),
следующий push не откатывает, но и смена тега без правки файла — в том
числе переезд Main correct на другое решение — сама на Polygon не уходит:
её нужно сделать в UI или через `polygon push --force`.

Если решение удалено или переименовано с прошлой загрузки, старый файл на
Polygon остаётся — шаг его не удаляет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import digest, solution_key
from orchestrator.problem_paths import is_removed_mark, problem_address
from orchestrator.spec import split_solution_stem

logger = logging.getLogger(__name__)

STEP_NAME = "upload_solutions"

# Приоритет выбора Main correct — именно в этом порядке. Значения — языки
# после нормализации через `_LANGUAGE_ALIASES`.
_MAIN_LANGUAGE_PRIORITY = ["py", "cpp", "java"]

# Варианты написания языка в имени файла -> имя из `_MAIN_LANGUAGE_PRIORITY`.
_LANGUAGE_ALIASES = {
    "python": "py",
    "python3": "py",
    "py3": "py",
    "c++": "cpp",
}

# Совпадает с `orchestrator.spec._ALLOWED_VERDICTS` (допустимые
# `solutions.wanted_verdicts`).
_VALID_VERDICTS = {"ok", "wa", "tl", "tl_ok", "tle", "ml", "mle", "re"}


@dataclass(frozen=True)
class _ParsedSolution:
    path: Path
    verdict: str
    language: str


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает каждое решение, отличающееся от базы синхронизации, с тегом
    из `_tag_for` (`ctx.spec` не используется).

    Обе зависимости (`create_problem`, `solutions_draft`), разбор имён и
    выбор Main correct — по всему набору и до первого сетевого вызова.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    solutions = _read_solutions(ctx.problem_id, ctx.problems_dir)

    parsed = [_parse_solution_filename(Path(name)) for name, _ in solutions]
    main_name = _choose_main(parsed)
    tags = {item.path.name: _tag_for(item, main_name) for item in parsed}

    changed = changed_items(ctx, _items(solutions))
    if not changed:
        return StepResult(NO_CHANGES_MESSAGE)

    sent = []
    for name, content in solutions:
        if solution_key(name) not in changed:
            continue
        client.call(
            "problem.saveSolution",
            {
                "problemId": str(polygon_id),
                "name": name,
                "tag": tags[name],
                "file": content,
            },
        )
        sent.append(f"{name}={tags[name]}")

    return StepResult(
        f"uploaded {len(sent)} of {len(solutions)} solution(s) to Polygon id={polygon_id}: "
        f"{', '.join(sent)}",
        changed,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`): новое или изменённое решение
    делает шаг "stale"."""
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=solution_key(""),
        read_items=lambda: _items(_read_solutions(problem_id, problems_dir)),
    )


def _items(solutions: list[tuple[str, bytes]]) -> dict[str, str]:
    """Только содержимое, без тега — см. докстринг модуля."""
    return {solution_key(name): digest(content) for name, content in solutions}


def _read_solutions(problem_id: str, problems_dir: Path) -> list[tuple[str, bytes]]:
    """[(имя, содержимое), ...] для всех файлов `solutions/` по алфавиту
    имён, кроме помеченных шагом как удалённые (`is_removed_mark`). Нет
    каталога или он пуст — `PolygonStepError`."""
    solutions_dir = Path(problems_dir) / problem_id / "solutions"
    if not solutions_dir.is_dir():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': directory {solutions_dir} not found — first "
            f"run the generative step solutions_draft (`orchestrator {address} llm generate "
            f"--step solutions_draft`)"
        )
    paths = sorted(
        (p for p in solutions_dir.iterdir() if p.is_file() and not is_removed_mark(p)),
        key=lambda p: p.name,
    )
    if not paths:
        raise PolygonStepError(f"'{problem_id}': no files in {solutions_dir}")
    return [(path.name, path.read_bytes()) for path in paths]


def _parse_solution_filename(path: Path) -> _ParsedSolution:
    """Разбирает `<verdict>_<language>_<description>_<author>.<ext>`.
    `description` может содержать подчёркивания — берутся только первые два
    токена как verdict/language, остальное не разбирается (author для тега
    не нужен); составной вердикт (`tl_ok`) — один токен
    (`split_solution_stem`). Минимум 3 токена в stem."""
    parts = split_solution_stem(path.stem)
    if len(parts) < 3:
        raise PolygonStepError(
            f"'{path.name}': file name does not match the pattern "
            "<verdict>_<language>_<description>_<author>.<ext> "
            f"(fewer than 3 '_'-separated tokens: {parts})"
        )
    verdict = parts[0].lower()
    language = _LANGUAGE_ALIASES.get(parts[1].lower(), parts[1].lower())
    if verdict not in _VALID_VERDICTS:
        raise PolygonStepError(
            f"'{path.name}': verdict '{verdict}' is not in the allowed set "
            f"{sorted(_VALID_VERDICTS)} — check the file name manually"
        )
    return _ParsedSolution(path=path, verdict=verdict, language=language)


def _choose_main(parsed: list[_ParsedSolution]) -> str:
    """Имя файла Main correct — первый по алфавиту `ok`-файл первого языка из
    `_MAIN_LANGUAGE_PRIORITY`, для которого такой файл есть. Если ни один
    язык из приоритета не встретился — WARNING и первый по алфавиту
    `ok`-файл (риск 1 в докстринге модуля), не ошибка."""
    ok_names = sorted(p.path.name for p in parsed if p.verdict == "ok")
    if not ok_names:
        raise PolygonStepError(
            "no solution with verdict 'ok' — nothing to pick as Main "
            "correct (expected at least one ok_* file)"
        )

    for lang in _MAIN_LANGUAGE_PRIORITY:
        candidates = sorted(
            p.path.name for p in parsed if p.verdict == "ok" and p.language == lang
        )
        if candidates:
            return candidates[0]

    logger.warning(
        "none of the priority languages %s found among ok solutions %s — "
        "the language in file names may be spelled differently than expected; "
        "Main correct is the alphabetically first one, check the choice in the Polygon UI",
        _MAIN_LANGUAGE_PRIORITY,
        ok_names,
    )
    return ok_names[0]


def _tag_for(parsed: _ParsedSolution, main_name: str) -> str:
    if parsed.path.name == main_name:
        return "MA"
    if parsed.verdict == "ok":
        return "OK"
    if parsed.verdict in ("tl", "tle"):
        return "TL"
    if parsed.verdict == "tl_ok":
        return "TO"
    if parsed.verdict == "wa":
        return "WA"
    if parsed.verdict in ("ml", "mle"):
        return "ML"
    return "RJ"  # re — "Incorrect"


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
