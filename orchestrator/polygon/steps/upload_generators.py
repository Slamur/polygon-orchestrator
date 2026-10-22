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
from orchestrator.polygon.sync import digest, generator_key

STEP_NAME = "upload_generators"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Загружает каждый `generators/*.cpp`, отличающийся от базы
    синхронизации (`ctx.spec` не используется).

    Обе зависимости (`create_problem`, `generators_and_script`) проверяются
    до первого сетевого вызова.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    generators = _read_generators(ctx.problem_id, ctx.outputs_dir)
    changed = changed_items(ctx, _items(generators))
    if not changed:
        return StepResult(NO_CHANGES_MESSAGE)

    sent = []
    for name, content in generators:
        if generator_key(name) not in changed:
            continue
        client.call(
            "problem.saveFile",
            {"problemId": str(polygon_id), "type": "source", "name": name, "file": content},
        )
        sent.append(name)

    return StepResult(
        f"uploaded {len(sent)} of {len(generators)} generator(s) ({', '.join(sent)}) "
        f"to Polygon id={polygon_id}",
        changed,
    )


def _compute_status(problem_id: str, outputs_dir: Path) -> tuple[str, str]:
    """По базе синхронизации (`sync_status`): новый или изменённый
    генератор делает шаг "stale"."""
    return sync_status(
        problem_id,
        outputs_dir,
        key_prefix=generator_key(""),
        read_items=lambda: _items(_read_generators(problem_id, outputs_dir)),
    )


def _items(generators: list[tuple[str, bytes]]) -> dict[str, str]:
    return {generator_key(name): digest(content) for name, content in generators}


def _read_generators(problem_id: str, outputs_dir: Path) -> list[tuple[str, bytes]]:
    """[(имя, содержимое), ...] для `generators/*.cpp` по алфавиту имён.
    Нет каталога или в нём нет ни одного `.cpp` — `PolygonStepError`."""
    generators_dir = Path(outputs_dir) / problem_id / "generators"
    if not generators_dir.is_dir():
        raise PolygonStepError(
            f"'{problem_id}': directory {generators_dir} not found — first "
            f"run the generative step generators_and_script (orchestrator "
            f"run {problem_id} --step generators_and_script)"
        )
    paths = sorted(generators_dir.glob("*.cpp"), key=lambda p: p.name)
    if not paths:
        raise PolygonStepError(
            f"'{problem_id}': no .cpp files in {generators_dir}"
        )
    return [(path.name, path.read_bytes()) for path in paths]


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
