"""Polygon-шаг 9 — загрузка всех файлов из `outputs/<problem_id>/solutions/`
(результат генеративного шага `solutions_draft`) в Polygon
(`problem.saveSolution`) с тегом вердикта для каждого.

Тег выводится из имени файла по схеме
`<verdict>_<language>_<description>_<author>.<ext>`
(`prompts/solutions_draft/system.md`, правило 5):

- ровно один `ok`-файл получает `MA` (Main correct — эталон для генерации
  ответов на тесты, `templates/tutorials/polygon.md`, "Solution files"),
  выбор — по приоритету языков `_MAIN_LANGUAGE_PRIORITY`, внутри языка — по
  алфавиту имён;
- остальные `ok` -> `OK`, `tl`/`tle` -> `TL`, `wa` -> `WA`, `ml`/`mle` -> `ML`;
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

Различение `TL` / "TL или OK" (`TO`, например, для решения без оптимизации
ввода/вывода) сознательно не автоматизировано: в контракте `solutions_draft`
нет машиночитаемого признака такого решения — правится автором вручную
после загрузки.

Все имена файлов разбираются и Main correct выбирается до первого сетевого
вызова: файл с неподходящим именем — `PolygonStepError`, а не тихий пропуск.

Если решение удалено или переименовано с прошлой загрузки, старый файл на
Polygon остаётся — шаг его не удаляет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state, record_polygon_step
from orchestrator.polygon.steps.base import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    STATUS_STALE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    require_polygon_state,
)

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
_VALID_VERDICTS = {"ok", "wa", "tl", "tle", "ml", "mle", "re"}


@dataclass(frozen=True)
class _ParsedSolution:
    path: Path
    verdict: str
    language: str


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveSolution`
    перезаписывает решения, а ручные правки в UI Polygon (например, смена
    тега) локально не видны — пропускать загрузку по локальной записи
    нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Загружает каждое решение с тегом из `_tag_for` (`ctx.spec` не
    используется); записывает sha256 загруженного набора и выданные теги в
    `polygon_state.json` — для `status`.

    Обе зависимости (`create_problem`, `solutions_draft`), разбор имён и
    выбор Main correct — до первого сетевого вызова. Запись делается только
    после успеха всех вызовов.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    solutions = _read_solutions(ctx.problem_id, ctx.outputs_dir)

    parsed = [_parse_solution_filename(Path(name)) for name, _ in solutions]
    main_name = _choose_main(parsed)
    tags = {item.path.name: _tag_for(item, main_name) for item in parsed}

    for name, content in solutions:
        client.call(
            "problem.saveSolution",
            {
                "problemId": str(polygon_id),
                "name": name,
                "tag": tags[name],
                "file": content,
            },
        )

    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "sha256": _content_hash(solutions),
            "tags": tags,
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    uploaded = ", ".join(f"{name}={tag}" for name, tag in tags.items())
    return (
        f"uploaded {len(solutions)} solution(s) to Polygon id={polygon_id}: "
        f"{uploaded}"
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если последний загруженный набор `solutions/` совпадает по
    sha256 (имена + содержимое) с текущим; "stale", если набор с тех пор
    изменился (или больше не читается); "not run", если шаг ещё ни разу не
    выполнялся успешно."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        return STATUS_NOT_RUN, ""

    try:
        solutions = _read_solutions(problem_id, outputs_dir)
    except PolygonStepError as exc:
        return STATUS_STALE, f"current solutions are unreadable: {exc}"
    current = _content_hash(solutions)
    if current != record["sha256"]:
        return STATUS_STALE, "solutions/ changed since the last upload"
    return STATUS_DONE, f"{len(solutions)} solution(s), sha256={current[:12]}"


def _read_solutions(problem_id: str, outputs_dir: Path) -> list[tuple[str, bytes]]:
    """[(имя, содержимое), ...] для всех файлов `solutions/` по алфавиту
    имён. Нет каталога или он пуст — `PolygonStepError`."""
    solutions_dir = Path(outputs_dir) / problem_id / "solutions"
    if not solutions_dir.is_dir():
        raise PolygonStepError(
            f"'{problem_id}': directory {solutions_dir} not found — first "
            f"run the generative step solutions_draft (orchestrator llm generate "
            f"{problem_id} --step solutions_draft)"
        )
    paths = sorted((p for p in solutions_dir.iterdir() if p.is_file()), key=lambda p: p.name)
    if not paths:
        raise PolygonStepError(f"'{problem_id}': no files in {solutions_dir}")
    return [(path.name, path.read_bytes()) for path in paths]


def _parse_solution_filename(path: Path) -> _ParsedSolution:
    """Разбирает `<verdict>_<language>_<description>_<author>.<ext>`.
    `description` может содержать подчёркивания — берутся только первые два
    токена как verdict/language, остальное не разбирается (author для тега
    не нужен). Минимум 3 токена в stem."""
    parts = path.stem.split("_")
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
        return "TL"  # не TO — см. докстринг модуля
    if parsed.verdict == "wa":
        return "WA"
    if parsed.verdict in ("ml", "mle"):
        return "ML"
    return "RJ"  # re — "Incorrect"


def _content_hash(solutions: list[tuple[str, bytes]]) -> str:
    """sha256 по именам и содержимому — переименование решения (а значит,
    возможно, и смена тега) тоже делает шаг "stale"."""
    digest = hashlib.sha256()
    for name, content in solutions:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
