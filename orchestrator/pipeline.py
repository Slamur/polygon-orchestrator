"""Прогон 4 шагов для одного `problem_id`, с учётом кэша (CLAUDE.md,
"Пакетный запуск", "Кэширование по хешу спека").

`run_pipeline` не вызывает модель, если это не нужно: перед каждым шагом
считает `input_hash`/`prompt_hash` (не запуская сам шаг) и проверяет
`cache.is_cache_valid(...)` — модель вызывается только если кэш невалиден
или `force=True`. Если шаг возвращает `status: uncertain`
(`StepUncertainError`), пайплайн останавливается для этого `problem_id`
(последующие шаги не запускаются), но не бросает исключение наружу — это
нужно, чтобы `run --all` (см. `cli.py`) продолжал прогонять остальные задачи,
даже если одна из них упёрлась в нехватку данных в спеке.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from orchestrator.cache import (
    UNCERTAIN_STATUS,
    compute_prompt_hash,
    is_cache_valid,
    load_cache_entry,
)
from orchestrator.spec import SpecValidationError, load_spec
from orchestrator.steps import (
    constraints_pick,
    generators_and_script,
    solutions_draft,
    statement_draft,
)
from orchestrator.steps.base import (
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepUncertainError,
)

SPECS_DIR = Path("specs")

# Порядок шагов фиксирован (CLAUDE.md, "Роль каждого генеративного шага"):
# каждый следующий шаг может зависеть от артефактов предыдущих.
STEP_ORDER: list[str] = [
    "statement_draft",
    "constraints_pick",
    "generators_and_script",
    "solutions_draft",
]

_STEP_MODULES: dict[str, ModuleType] = {
    "statement_draft": statement_draft,
    "constraints_pick": constraints_pick,
    "generators_and_script": generators_and_script,
    "solutions_draft": solutions_draft,
}

# Имя артефакта constraints_pick, который шаги 3 и 4 читают с диска как
# upstream-вход (см. CLAUDE.md, "Кэширование по хешу спека"). Совпадает с
# generators_and_script.CONSTRAINTS_ARTIFACT — дублируем константу здесь,
# чтобы pipeline.py не тянул лишнюю зависимость от шага 3 ради одной строки.
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# Статусы StepOutcome.status:
OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_CONFIRMED = "confirmed"
OUTCOME_PROPOSED = "proposed"
OUTCOME_UNCERTAIN = "uncertain"
OUTCOME_SKIPPED_OPTIONAL = "skipped_optional"


@dataclass
class StepOutcome:
    """Что произошло с одним шагом в рамках одного прогона пайплайна."""

    step_name: str
    status: str
    detail: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Результат прогона всех шагов для одного `problem_id`."""

    problem_id: str
    outcomes: list[StepOutcome] = field(default_factory=list)
    stopped_uncertain: bool = False
    spec_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.spec_error is None and not self.stopped_uncertain


def _artifact_present(path: Path) -> bool:
    """Проверяет, что файл/каталог, ожидаемый шагом, реально на месте.

    Для шага `solutions_draft` `path` — каталог с произвольным набором
    файлов (модель сама называет решения), поэтому "на месте" означает
    "существует и не пуст"; для остальных шагов `path` — конкретный файл.
    """
    if path.is_dir():
        return any(path.iterdir())
    return path.exists()


def run_pipeline(
    problem_id: str,
    *,
    force: bool = False,
    only_step: Optional[str] = None,
    specs_dir: Path = SPECS_DIR,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> PipelineResult:
    """Прогоняет шаги `problem_id` по порядку `STEP_ORDER`.

    `only_step`, если задан, ограничивает прогон одним шагом (используется
    CLI-флагом `--step`); шаги перед ним всё равно не запускаются, но
    upstream-артефакты, нужные ему (`constraints.yaml`), читаются с диска —
    предполагается, что предыдущие шаги уже отработали (см. `cli.py`
    `run <problem_id> --step NAME`, требующий этого руками).

    `force=True` обходит кэш полностью — каждый нужный шаг вызывает модель
    заново, независимо от `input_hash`/`prompt_hash`.

    Останавливается (не бросая исключение) на первом `StepUncertainError` —
    `result.stopped_uncertain=True`, `result.outcomes` содержит всё, что
    успело отработать. Ошибка валидации спека (`SpecValidationError`) тоже не
    прокидывается наружу — записывается в `result.spec_error`, чтобы вызывающий
    `run --all` мог продолжить с другими `problem_id`.
    """
    try:
        spec = load_spec(Path(specs_dir) / f"{problem_id}.yaml")
    except SpecValidationError as exc:
        return PipelineResult(problem_id=problem_id, spec_error=str(exc))

    result = PipelineResult(problem_id=problem_id)
    upstream_artifacts: dict[str, Any] = {}

    steps_to_run = STEP_ORDER if only_step is None else [only_step]

    for step_name in steps_to_run:
        module = _STEP_MODULES[step_name]

        if step_name == "solutions_draft" and spec.solutions is None:
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_SKIPPED_OPTIONAL,
                    "в спеке нет секции 'solutions' — черновики решений не заказаны автором",
                )
            )
            continue

        # Шаги 3 и 4 зависят от constraints.yaml с диска. Если запускаем не с
        # начала (только --step или пропущенные по кэшу предыдущие шаги),
        # артефакт нужно подхватить, а не требовать, чтобы это делал шаг 2
        # в этом же прогоне.
        if step_name in ("generators_and_script", "solutions_draft") and (
            CONSTRAINTS_ARTIFACT not in upstream_artifacts
        ):
            constraints_path = constraints_pick.primary_artifact_path(
                problem_id, spec, outputs_dir=outputs_dir
            )
            if constraints_path.exists():
                upstream_artifacts[CONSTRAINTS_ARTIFACT] = constraints_path.read_text(
                    encoding="utf-8"
                )

        try:
            input_hash = module.compute_input_hash(problem_id, spec, upstream_artifacts)
        except ValueError as exc:
            # Нет constraints.yaml (шаг 2 ещё не запускался успешно) — это
            # ошибка конфигурации запуска, а не uncertain: пайплайн должен
            # остановиться так же, как остановился бы run_step с той же
            # ValueError, но без падения всего процесса run --all.
            result.outcomes.append(StepOutcome(step_name, "error", str(exc)))
            result.stopped_uncertain = True
            return result

        prompt_hash = compute_prompt_hash(
            step_name,
            prompts_dir=prompts_dir,
            templates_dir=templates_dir,
            extra_context_documents=module.extra_context_documents(spec),
        )
        cache_entry = load_cache_entry(problem_id, step_name, outputs_dir=outputs_dir)
        primary_path = module.primary_artifact_path(problem_id, spec, outputs_dir=outputs_dir)

        cache_hit = (
            not force
            and is_cache_valid(cache_entry, input_hash, prompt_hash)
            and _artifact_present(primary_path)
        )

        if cache_hit:
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CACHE_HIT,
                    f"вход не изменился (input_hash={input_hash[:12]}…) — модель не вызывалась",
                )
            )
        else:
            try:
                step_result = module.run_step(
                    problem_id,
                    spec,
                    dict(upstream_artifacts) or None,
                    prompts_dir=prompts_dir,
                    outputs_dir=outputs_dir,
                    templates_dir=templates_dir,
                )
            except StepUncertainError as exc:
                result.outcomes.append(
                    StepOutcome(step_name, OUTCOME_UNCERTAIN, str(exc), notes=exc.notes)
                )
                result.stopped_uncertain = True
                return result

            result.outcomes.append(
                StepOutcome(step_name, step_result.status, notes=step_result.notes)
            )

        if step_name == "constraints_pick":
            upstream_artifacts[CONSTRAINTS_ARTIFACT] = primary_path.read_text(encoding="utf-8")

    return result


# Состояния StepStatus.state для `orchestrator status` (CLAUDE.md, "Пакетный
# запуск": "печатает таблицу 'шаг -> cache hit / stale / not run'").
STATUS_CACHE_HIT = "cache hit"
STATUS_STALE = "stale"
STATUS_NOT_RUN = "not run"
STATUS_UNCERTAIN = "uncertain"
STATUS_SKIPPED_OPTIONAL = "skipped (optional)"
STATUS_BLOCKED = "blocked"


@dataclass
class StepStatus:
    """Состояние одного шага для `orchestrator status` — считается только по
    `.cache/*.json` и текущим хешам, без вызова модели."""

    step_name: str
    state: str
    detail: str = ""


def compute_step_statuses(
    problem_id: str,
    *,
    specs_dir: Path = SPECS_DIR,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[list[StepStatus], Optional[str]]:
    """Статус каждого шага для `problem_id`: cache hit / stale / not run /
    uncertain / blocked / skipped (optional) — без единого вызова модели.

    Использует те же `compute_input_hash`/`compute_prompt_hash`/
    `is_cache_valid`, что и `run_pipeline`, поэтому "cache hit" здесь и
    реальный пропуск шага в `run_pipeline` всегда согласованы (CLAUDE.md,
    "Пакетный запуск": "используя те же хеши, что и run").

    Возвращает `(статусы, spec_error)`: если спек не проходит валидацию,
    статусы — пустой список, а `spec_error` — текст ошибки.
    """
    try:
        spec = load_spec(Path(specs_dir) / f"{problem_id}.yaml")
    except SpecValidationError as exc:
        return [], str(exc)

    statuses: list[StepStatus] = []
    upstream_artifacts: dict[str, Any] = {}

    for step_name in STEP_ORDER:
        module = _STEP_MODULES[step_name]

        if step_name == "solutions_draft" and spec.solutions is None:
            statuses.append(
                StepStatus(
                    step_name,
                    STATUS_SKIPPED_OPTIONAL,
                    "в спеке нет секции 'solutions' — черновики решений не заказаны",
                )
            )
            continue

        if step_name in ("generators_and_script", "solutions_draft") and (
            CONSTRAINTS_ARTIFACT not in upstream_artifacts
        ):
            constraints_path = constraints_pick.primary_artifact_path(
                problem_id, spec, outputs_dir=outputs_dir
            )
            if constraints_path.exists():
                upstream_artifacts[CONSTRAINTS_ARTIFACT] = constraints_path.read_text(
                    encoding="utf-8"
                )

        try:
            input_hash = module.compute_input_hash(problem_id, spec, upstream_artifacts)
        except ValueError as exc:
            statuses.append(StepStatus(step_name, STATUS_BLOCKED, str(exc)))
            continue

        cache_entry = load_cache_entry(problem_id, step_name, outputs_dir=outputs_dir)
        primary_path = module.primary_artifact_path(problem_id, spec, outputs_dir=outputs_dir)

        if cache_entry is None:
            statuses.append(StepStatus(step_name, STATUS_NOT_RUN))
        elif cache_entry.status == UNCERTAIN_STATUS:
            statuses.append(
                StepStatus(
                    step_name,
                    STATUS_UNCERTAIN,
                    "прошлый запуск закончился status: uncertain — поправьте спек и запустите заново",
                )
            )
        else:
            prompt_hash = compute_prompt_hash(
                step_name,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
                extra_context_documents=module.extra_context_documents(spec),
            )
            if is_cache_valid(cache_entry, input_hash, prompt_hash) and _artifact_present(
                primary_path
            ):
                statuses.append(StepStatus(step_name, STATUS_CACHE_HIT))
            elif not _artifact_present(primary_path):
                statuses.append(
                    StepStatus(step_name, STATUS_STALE, "артефакт отсутствует на диске")
                )
            elif cache_entry.input_hash != input_hash:
                statuses.append(
                    StepStatus(step_name, STATUS_STALE, "спек (или апстрим-артефакт) изменился")
                )
            else:
                statuses.append(StepStatus(step_name, STATUS_STALE, "промпт-шаблон изменился"))

        if step_name == "constraints_pick" and primary_path.exists():
            upstream_artifacts[CONSTRAINTS_ARTIFACT] = primary_path.read_text(encoding="utf-8")

    return statuses, None
