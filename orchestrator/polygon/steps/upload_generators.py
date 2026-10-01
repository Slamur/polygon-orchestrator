"""Polygon-шаг 7 — загрузка всех `outputs/<problem_id>/generators/*.cpp`
(результат генеративного шага `generators_and_script`; имена файлов модель
выбирает сама) в Polygon как source-файлов (`problem.saveFile`,
`type=source`).

`sourceType` не передаётся — как и в `upload_validator`, предполагается, что
Polygon определит язык по расширению `.cpp`.

Файлы загружаются в алфавитном порядке имён — ради детерминированности
тестов и логов; самому Polygon порядок загрузки source-файлов не важен.
Файлы с другими расширениями в `generators/` (например, случайно попавший
туда `.h`) не загружаются.

Если генератор удалён или переименован с прошлой загрузки, старый файл на
Polygon остаётся — шаг его не удаляет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
import hashlib
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

STEP_NAME = "upload_generators"


def _check_done(ctx: StepContext) -> str | None:
    """Шаг выполняется при каждом запуске: `problem.saveFile` перезаписывает
    файлы, а ручные правки в UI Polygon локально не видны — пропускать
    загрузку по локальной записи нельзя."""
    return None


def _execute(ctx: StepContext, client: PolygonClient) -> str:
    """Загружает каждый `generators/*.cpp` (`ctx.spec` не используется);
    записывает sha256 загруженного набора файлов в `polygon_state.json` —
    для `status`.

    Обе зависимости (`create_problem`, `generators_and_script`) проверяются
    до первого сетевого вызова. Запись делается только после успеха всех
    вызовов: если упал какой-то `saveFile`, шаг остаётся "not run"/"stale"
    и повторится целиком при следующем запуске.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    generators = _read_generators(ctx.problem_id, ctx.outputs_dir)

    for name, content in generators:
        client.call(
            "problem.saveFile",
            {"problemId": str(polygon_id), "type": "source", "name": name, "file": content},
        )

    record_polygon_step(
        ctx.problem_id,
        STEP_NAME,
        {
            "sha256": _content_hash(generators),
            "files": [name for name, _ in generators],
            "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        outputs_dir=ctx.outputs_dir,
    )
    names = ", ".join(name for name, _ in generators)
    return (
        f"uploaded {len(generators)} generator(s) ({names}) to Polygon "
        f"id={polygon_id}"
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """"done", если последний загруженный набор `generators/*.cpp` совпадает
    по sha256 (имена + содержимое) с текущим; "stale", если набор с тех пор
    изменился (или больше не читается); "not run", если шаг ещё ни разу не
    выполнялся успешно."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    record = state.steps.get(STEP_NAME) if state is not None else None
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        return STATUS_NOT_RUN, ""

    try:
        generators = _read_generators(problem_id, outputs_dir)
    except PolygonStepError as exc:
        return STATUS_STALE, f"текущие генераторы не читаются: {exc}"
    current = _content_hash(generators)
    if current != record["sha256"]:
        return STATUS_STALE, "generators/ изменился после последней загрузки"
    return STATUS_DONE, f"{len(generators)} generator(s), sha256={current[:12]}"


def _read_generators(problem_id: str, outputs_dir: Path) -> list[tuple[str, bytes]]:
    """[(имя, содержимое), ...] для `generators/*.cpp` по алфавиту имён.
    Нет каталога или в нём нет ни одного `.cpp` — `PolygonStepError`."""
    generators_dir = Path(outputs_dir) / problem_id / "generators"
    if not generators_dir.is_dir():
        raise PolygonStepError(
            f"'{problem_id}': не найден каталог {generators_dir} — сначала "
            f"выполните генеративный шаг generators_and_script (orchestrator "
            f"run {problem_id} --step generators_and_script)"
        )
    paths = sorted(generators_dir.glob("*.cpp"), key=lambda p: p.name)
    if not paths:
        raise PolygonStepError(
            f"'{problem_id}': в {generators_dir} нет ни одного .cpp-файла"
        )
    return [(path.name, path.read_bytes()) for path in paths]


def _content_hash(generators: list[tuple[str, bytes]]) -> str:
    """sha256 по именам и содержимому — переименование генератора тоже
    делает шаг "stale"."""
    digest = hashlib.sha256()
    for name, content in generators:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
)
