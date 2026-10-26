"""Прогон генеративных шагов для одного `problem_id`, с учётом кэша (CLAUDE.md,
"Запуск", "Кэширование по хешу спека"), статус кэша шагов
(`compute_step_statuses`) и принятие текущих файлов без вызова модели
(`accept_steps`).

`run_pipeline` не вызывает модель, если это не нужно: перед каждым шагом
считает `input_hash`/`prompt_hash` (не запуская сам шаг) и проверяет
`cache.is_cache_valid(...)` — модель вызывается только если кэш невалиден,
`force=True` или `fresh=True`. Если файлы шага уже есть на диске, шаг
запускается в режиме доработки (`steps.base.StepRevision`): модель получает
их и то, что изменилось во входе шага; `fresh=True` — с нуля, без текущих
файлов. Если шаг возвращает `status: uncertain`
(`StepUncertainError`), пайплайн останавливается для этого `problem_id`
(последующие шаги не запускаются), но не бросает исключение наружу: это
ожидаемый исход, а не сбой, и CLI печатает его как результат шага.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from orchestrator import model_router
from orchestrator.model_contracts import ModelResponse
from orchestrator.cache import (
    UNCERTAIN_STATUS,
    CacheEntry,
    compute_input_values,
    input_parts_from_values,
    compute_prompt_hash,
    compute_prompt_parts,
    describe_changed_parts,
    PendingResponse,
    is_cache_valid,
    load_cache_entry,
    discard_pending_response,
    load_pending_response,
    save_cache_entry,
)
from orchestrator.problem_paths import is_removed_mark, problem_address
from orchestrator.references import SpecReferenceError
from orchestrator.spec import SpecValidationError, load_problem_spec
from orchestrator.step_order import STEP_ORDER
from orchestrator.steps import (
    checker_draft,
    constraints_pick,
    generators_and_script,
    interactor_draft,
    solutions_draft,
    statement_draft,
)
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    CONFLICTS_REPORT,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepRevision,
    StepUncertainError,
)

logger = logging.getLogger(__name__)

_STEP_MODULES: dict[str, ModuleType] = {
    "statement_draft": statement_draft,
    "constraints_pick": constraints_pick,
    "generators_and_script": generators_and_script,
    "checker_draft": checker_draft,
    "interactor_draft": interactor_draft,
    "solutions_draft": solutions_draft,
}

# Имя артефакта constraints_pick, который generators_and_script и
# solutions_draft читают с диска как upstream-вход (см. CLAUDE.md,
# "Кэширование по хешу спека"). Совпадает с
# generators_and_script.CONSTRAINTS_ARTIFACT — дублируем константу здесь,
# чтобы pipeline.py не тянул лишнюю зависимость от шага 3 ради одной строки.
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# Статусы StepOutcome.status:
OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_CONFIRMED = "confirmed"
OUTCOME_PROPOSED = "proposed"
OUTCOME_UNCERTAIN = "uncertain"
OUTCOME_SKIPPED_OPTIONAL = "skipped_optional"
OUTCOME_ERROR = "error"
OUTCOME_CONFLICT = "conflict"
OUTCOME_NO_PENDING = "no_pending_response"


@dataclass
class StepOutcome:
    """Что произошло с одним шагом в рамках одного прогона пайплайна."""

    step_name: str
    status: str
    detail: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)
    # Режим доработки (`steps.base.StepResult`): файлы, помеченные
    # `.removed`, переименования и каталог с прежними версиями файлов.
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    backup_dir: Optional[Path] = None
    # Что из ответа модели не применено (имя файла, причина) и куда отложены
    # версии модели (`steps.base.StepResult`).
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    conflicts_dir: Optional[Path] = None


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
    "существует и в нём есть что-то, кроме помеченного `.removed`"; для
    остальных шагов `path` — конкретный файл.
    """
    if path.is_dir():
        return any(not is_removed_mark(child) for child in path.iterdir())
    return path.exists()




@dataclass
class _StepHashes:
    """Текущие хеши входа шага — то, с чем сравнивается `CacheEntry`."""

    input_hash: str
    prompt_hash: str
    input_parts: dict[str, str]
    prompt_parts: dict[str, str]
    model: str
    input_values: dict[str, Any]


def _optional_skip_detail(step_name: str, spec: Any) -> Optional[str]:
    """Почему опциональный шаг не нужен этому спеку; `None` — нужен."""
    if step_name == "solutions_draft" and spec.solutions is None:
        return "spec has no 'solutions' section — solution drafts were not requested by the author"
    if step_name == "checker_draft" and spec.checker.custom_needed is False:
        return "checker.custom_needed=false — a standard checker is used, no draft requested"
    if step_name == "interactor_draft" and not spec.interactive:
        return "spec has no 'interactor' section — the problem is not interactive"
    return None


def _load_constraints_upstream(
    step_name: str,
    problem_id: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    problems_dir: Path,
) -> None:
    """generators_and_script и solutions_draft зависят от constraints.yaml с
    диска. Если constraints_pick в этом прогоне не запускался (только --step
    или пропущен по кэшу), артефакт подхватывается с диска."""
    if step_name not in ("generators_and_script", "solutions_draft"):
        return
    if CONSTRAINTS_ARTIFACT in upstream_artifacts:
        return
    constraints_path = constraints_pick.primary_artifact_path(
        problem_id, spec, problems_dir=problems_dir
    )
    if constraints_path.exists():
        upstream_artifacts[CONSTRAINTS_ARTIFACT] = constraints_path.read_text(encoding="utf-8")


def _compute_step_hashes(
    problem_id: str,
    step_name: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    *,
    prompts_dir: Path,
    templates_dir: Path,
) -> _StepHashes:
    """Хеши шага без вызова модели. `ValueError` — нет нужного
    upstream-артефакта (`constraints.yaml`), `SpecReferenceError` —
    референс спека не найден или ничего не даёт шагу."""
    module = _STEP_MODULES[step_name]
    input_hash = module.compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*module.hash_inputs(problem_id, spec, upstream_artifacts))
    extra_documents = module.extra_context_documents(spec, templates_dir=templates_dir)
    prompt_kwargs = dict(
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_documents,
    )
    return _StepHashes(
        input_hash=input_hash,
        prompt_hash=compute_prompt_hash(step_name, **prompt_kwargs),
        input_parts=input_parts_from_values(input_values),
        prompt_parts=compute_prompt_parts(step_name, **prompt_kwargs),
        model=model_router.STEP_TO_MODEL[step_name],
        input_values=input_values,
    )


def _stale_reason(entry: CacheEntry, hashes: _StepHashes) -> str:
    """Почему кэш шага невалиден (при уже известном, что он невалиден и
    `entry.status` — не `uncertain`): что именно изменилось — по хешам
    частей, если они есть в записи кэша."""
    reasons: list[str] = []
    parts_unknown = False
    if entry.model != hashes.model:
        reasons.append(f"model changed ({entry.model} -> {hashes.model})")
    for summary, old_hash, new_hash, old_parts, new_parts in (
        (
            "spec (or upstream artifact) changed",
            entry.input_hash,
            hashes.input_hash,
            entry.input_parts,
            hashes.input_parts,
        ),
        (
            "prompt template changed",
            entry.prompt_hash,
            hashes.prompt_hash,
            entry.prompt_parts,
            hashes.prompt_parts,
        ),
    ):
        if old_hash == new_hash:
            continue
        if old_parts is None:
            parts_unknown = True
            reasons.append(summary)
            continue
        changed = describe_changed_parts(old_parts, new_parts)
        reasons.append(f"{summary}: {', '.join(changed)}" if changed else summary)
    reason = "; ".join(reasons) or "cache entry does not match"
    if parts_unknown:
        reason += " (which parts — unknown: the cache entry predates per-part hashes)"
    return reason


def _revision_for_step(
    module: ModuleType,
    problem_id: str,
    spec: Any,
    cache_entry: Optional[CacheEntry],
    hashes: _StepHashes,
    problems_dir: Path,
) -> Optional[StepRevision]:
    """Режим доработки, если файлы шага есть на диске; иначе `None` (с нуля).
    Что изменилось во входе — по `input_values` записи кэша; смена модели и
    промптов/документов — только именами (`other_changes`)."""
    current = module.current_artifacts(problem_id, spec, problems_dir=problems_dir)
    if not current:
        return None
    other_changes: list[str] = []
    if cache_entry is not None:
        if cache_entry.model != hashes.model:
            other_changes.append(f"model: {cache_entry.model} -> {hashes.model}")
        if cache_entry.prompt_hash != hashes.prompt_hash:
            changed = describe_changed_parts(cache_entry.prompt_parts, hashes.prompt_parts)
            other_changes.extend(changed or ["prompt templates or attached documents"])
    return StepRevision(
        current_paths=current,
        previous_input_values=cache_entry.input_values if cache_entry is not None else None,
        other_changes=other_changes,
    )


def _pending_revision(
    pending: PendingResponse,
    module: ModuleType,
    problem_id: str,
    spec: Any,
    cache_entry: Optional[CacheEntry],
    hashes: _StepHashes,
    problems_dir: Path,
) -> tuple[Optional[StepRevision], Optional[str]]:
    """Режим, в котором применяется сохранённый ответ (`_revision_for_step`,
    если ответ получен в режиме доработки), или почему его нельзя применить
    к текущему входу шага: `(revision, None)` или `(None, причина)`."""
    try:
        ModelResponse(**pending.response)
    except TypeError as exc:
        return None, f"the saved response is malformed ({exc})"
    entry = pending.cache_entry
    if not is_cache_valid(entry, hashes.input_hash, hashes.prompt_hash, hashes.model):
        return None, (
            "the saved response was received for a different input of the step "
            f"({_stale_reason(entry, hashes)})"
        )
    if not pending.revised:
        return None, None
    revision = _revision_for_step(module, problem_id, spec, cache_entry, hashes, problems_dir)
    if revision is None:
        return None, (
            "the saved response revises the current files of the step, "
            "but there are none on disk now"
        )
    return revision, None


def _run_step_into(
    result: PipelineResult,
    module: ModuleType,
    step_name: str,
    problem_id: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    *,
    revision: Optional[StepRevision],
    saved_response: Optional[ModelResponse],
    detail: str,
    prompts_dir: Path,
    problems_dir: Path,
    templates_dir: Path,
) -> bool:
    """Запускает шаг и дописывает его исход в `result`. `False` — пайплайн
    остановлен (`uncertain` или ответ модели не прошёл проверку)."""
    try:
        step_result = module.run_step(
            problem_id,
            spec,
            dict(upstream_artifacts) or None,
            revision=revision,
            saved_response=saved_response,
            prompts_dir=prompts_dir,
            problems_dir=problems_dir,
            templates_dir=templates_dir,
        )
    except StepUncertainError as exc:
        result.outcomes.append(
            StepOutcome(step_name, OUTCOME_UNCERTAIN, str(exc), notes=exc.notes)
        )
        result.stopped_uncertain = True
        return False
    except ValueError as exc:
        # Ответ модели не прошёл проверку — в `problems/` ничего не записано.
        # Сам ответ сохранён (`PendingResponse`): после правки причины его
        # можно применить без нового вызова модели.
        message = str(exc)
        if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
            message += (
                " — nothing written; the model response is saved, "
                f"'orchestrator {problem_address(problems_dir, problem_id)} "
                f"llm apply --step {step_name}' applies it without calling the model"
            )
        result.outcomes.append(StepOutcome(step_name, OUTCOME_ERROR, message))
        result.stopped_uncertain = True
        return False

    conflicts_dir = step_result.conflicts_dir
    if conflicts_dir is not None:
        address = problem_address(problems_dir, problem_id)
        resolve = (
            f"{len(step_result.conflicts)} part(s) of the response not applied "
            f"(status: {step_result.status}) — see {conflicts_dir / CONFLICTS_REPORT}; "
            "resolve by hand, delete the directory and run "
            f"'orchestrator {address} llm accept --step {step_name}'"
        )
        if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
            resolve += (
                " (nothing is applied and the response is saved: after fixing the cause "
                f"'orchestrator {address} llm apply --step {step_name}' applies it)"
            )
        detail = f"{detail}; {resolve}" if detail else resolve
    result.outcomes.append(
        StepOutcome(
            step_name,
            OUTCOME_CONFLICT if conflicts_dir is not None else step_result.status,
            detail,
            notes=step_result.notes,
            removed=step_result.removed,
            renamed=step_result.renamed,
            backup_dir=step_result.backup_dir,
            conflicts=step_result.conflicts,
            conflicts_dir=conflicts_dir,
        )
    )
    if conflicts_dir is not None:
        # Следующие шаги зависят от файлов этого — пока автор не разберёт
        # конфликты, дальше не идём.
        result.stopped_uncertain = True
        return False
    return True


def run_pipeline(
    problem_id: str,
    *,
    force: bool = False,
    fresh: bool = False,
    apply_pending: bool = False,
    only_step: Optional[str] = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> PipelineResult:
    """Прогоняет шаги `problem_id` по порядку `STEP_ORDER`.

    `only_step`, если задан, ограничивает прогон одним шагом (используется
    CLI-флагом `--step`); шаги перед ним всё равно не запускаются, но
    upstream-артефакты, нужные ему (`constraints.yaml`), читаются с диска —
    предполагается, что предыдущие шаги уже отработали (см. `cli.py`
    `run <problem_id> --step NAME`, требующий этого руками).

    `force=True` обходит кэш полностью — каждый нужный шаг вызывает модель
    заново, независимо от `input_hash`/`prompt_hash`. Шаг, файлы которого
    уже есть на диске, запускается в режиме доработки (`_revision_for_step`);
    `fresh=True` — с нуля, без текущих файлов, и, как `force`, независимо от
    кэша (просьба "с нуля" при валидном кэше иначе молча ничего бы не
    сделала). Если шаг перезапущен
    без `force` из-за устаревшего кэша, в `StepOutcome.detail` — что именно
    изменилось (`_stale_reason`).

    `apply_pending=True` (`llm apply`) — модель не вызывается вовсе: шаги с
    сохранённым неприменённым ответом (`cache.PendingResponse` — ответ, не
    прошедший проверку) применяют его, если он получен на текущий вход шага
    (те же хеши, что у кэша) и в том же режиме (доработка или с нуля);
    остальные шаги пропускаются.

    Останавливается (не бросая исключение) на первом `StepUncertainError` —
    `result.stopped_uncertain=True`, `result.outcomes` содержит всё, что
    успело отработать. Так же — на ответе модели, не прошедшем проверку
    (`ValueError` шага; ответ сохранён для `apply_pending`). Ошибка валидации спека (`SpecValidationError`) тоже не
    прокидывается наружу — записывается в `result.spec_error`, и CLI печатает
    её вместо результатов шагов.
    """
    try:
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return PipelineResult(problem_id=problem_id, spec_error=str(exc))

    result = PipelineResult(problem_id=problem_id)
    upstream_artifacts: dict[str, Any] = {}

    steps_to_run = STEP_ORDER if only_step is None else [only_step]

    for step_name in steps_to_run:
        module = _STEP_MODULES[step_name]
        logger.info("[%s] step started", step_name)

        skip_detail = _optional_skip_detail(step_name, spec)
        if skip_detail is not None:
            result.outcomes.append(StepOutcome(step_name, OUTCOME_SKIPPED_OPTIONAL, skip_detail))
            continue

        pending = None
        if apply_pending:
            pending = load_pending_response(problem_id, step_name, problems_dir=problems_dir)
            if pending is None:
                result.outcomes.append(
                    StepOutcome(
                        step_name, OUTCOME_NO_PENDING, "no saved model response — nothing to apply"
                    )
                )
                continue

        _load_constraints_upstream(step_name, problem_id, spec, upstream_artifacts, problems_dir)

        try:
            hashes = _compute_step_hashes(
                problem_id,
                step_name,
                spec,
                upstream_artifacts,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
            )
        except (ValueError, SpecReferenceError) as exc:
            # ValueError — нет constraints.yaml (constraints_pick ещё не
            # запускался успешно), SpecReferenceError — референс спека не
            # найден: спек или запуск нужно поправить. Это не uncertain, но
            # пайплайн останавливается так же — с сообщением, а не traceback.
            result.outcomes.append(StepOutcome(step_name, OUTCOME_ERROR, str(exc)))
            result.stopped_uncertain = True
            return result

        cache_entry = load_cache_entry(problem_id, step_name, problems_dir=problems_dir)
        primary_path = module.primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
        artifact_present = _artifact_present(primary_path)

        if pending is not None:
            revision, problem = _pending_revision(
                pending, module, problem_id, spec, cache_entry, hashes, problems_dir
            )
            if problem is not None:
                result.outcomes.append(
                    StepOutcome(
                        step_name,
                        OUTCOME_ERROR,
                        f"{problem} — run "
                        f"'orchestrator {problem_address(problems_dir, problem_id)} "
                        f"llm generate --step {step_name}'",
                    )
                )
                result.stopped_uncertain = True
                return result
            if not _run_step_into(
                result,
                module,
                step_name,
                problem_id,
                spec,
                upstream_artifacts,
                revision=revision,
                saved_response=ModelResponse(**pending.response),
                detail="saved model response applied, model not called",
                prompts_dir=prompts_dir,
                problems_dir=problems_dir,
                templates_dir=templates_dir,
            ):
                return result
        elif cache_entry is not None and cache_entry.conflicts is not None and not (
            force or fresh
        ):
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CONFLICT,
                    _conflicts_detail(problem_id, step_name, cache_entry, problems_dir)
                    + " (or 'llm generate --force' to call the model again)",
                )
            )
            result.stopped_uncertain = True
            return result
        elif (
            not (force or fresh)
            and is_cache_valid(
                cache_entry, hashes.input_hash, hashes.prompt_hash, hashes.model
            )
            and artifact_present
        ):
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CACHE_HIT,
                    f"input unchanged (input_hash={hashes.input_hash[:12]}…) — model not called",
                )
            )
        else:
            if fresh:
                rerun_reason = "from scratch (--fresh)"
            elif force:
                rerun_reason = "forced (--force)"
            elif cache_entry is None or cache_entry.status == UNCERTAIN_STATUS:
                rerun_reason = ""
            elif not artifact_present:
                rerun_reason = "regenerated: artifact is missing on disk"
            else:
                rerun_reason = f"regenerated: {_stale_reason(cache_entry, hashes)}"
            revision = (
                None
                if fresh
                else _revision_for_step(
                    module, problem_id, spec, cache_entry, hashes, problems_dir
                )
            )
            if revision is not None:
                mode = f"revising {len(revision.current_paths)} current file(s)"
                rerun_reason = f"{rerun_reason}; {mode}" if rerun_reason else mode
            if rerun_reason:
                logger.info("[%s] %s", step_name, rerun_reason)
            if not _run_step_into(
                result,
                module,
                step_name,
                problem_id,
                spec,
                upstream_artifacts,
                revision=revision,
                saved_response=None,
                detail=rerun_reason,
                prompts_dir=prompts_dir,
                problems_dir=problems_dir,
                templates_dir=templates_dir,
            ):
                return result

        if step_name == "constraints_pick":
            upstream_artifacts[CONSTRAINTS_ARTIFACT] = primary_path.read_text(encoding="utf-8")

    return result


# Состояния StepStatus.state для `orchestrator <задача> llm status` (CLAUDE.md, "Запуск":
# "печатает таблицу 'шаг -> cache hit / stale / not run'").
STATUS_CACHE_HIT = "cache hit"
STATUS_STALE = "stale"
STATUS_NOT_RUN = "not run"
STATUS_UNCERTAIN = "uncertain"
STATUS_CONFLICT = "conflict"
STATUS_SKIPPED_OPTIONAL = "skipped (optional)"
STATUS_BLOCKED = "blocked"

# Дополнительные состояния StepStatus.state для `orchestrator <задача> llm accept`.
STATUS_ACCEPTED = "accepted"
STATUS_NOT_ACCEPTED = "not accepted"


@dataclass
class StepStatus:
    """Состояние одного шага для `orchestrator <задача> llm status`/`llm accept` —
    считается только по `.cache/*.json`, файлам и текущим хешам, без вызова
    модели."""

    step_name: str
    state: str
    detail: str = ""


def compute_step_statuses(
    problem_id: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[list[StepStatus], Optional[str]]:
    """Статус каждого шага для `problem_id`: cache hit / stale / not run /
    uncertain / blocked / skipped (optional) — без единого вызова модели.

    Использует те же хеши (`_compute_step_hashes`) и `is_cache_valid`, что и
    `run_pipeline`, поэтому "cache hit" здесь и реальный пропуск шага в
    `run_pipeline` всегда согласованы (CLAUDE.md, "Запуск":
    "используя те же хеши, что и run"). У `stale` в `detail` — что именно
    изменилось (`_stale_reason`).

    Возвращает `(статусы, spec_error)`: если спек не проходит валидацию,
    статусы — пустой список, а `spec_error` — текст ошибки.
    """
    return _walk_steps(
        problem_id,
        _status_of_step,
        only_step=None,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
    )


def accept_steps(
    problem_id: str,
    *,
    only_step: Optional[str] = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[list[StepStatus], Optional[str]]:
    """`orchestrator <задача> llm accept`: принимает текущие артефакты шагов как
    актуальные — перезаписывает кэш устаревших (`stale`) шагов текущими
    хешами, без вызова модели. Нужно, когда изменилось то, что не должно
    менять результат (рефакторинг промптов/шаблонов, смена раскладки
    `templates/`), или когда автор поправил спек под уже исправленные
    руками файлы: после этого `llm generate` перезапускает только шаги,
    вход которых изменится уже после принятия.

    Не принимаются: шаги без артефакта на диске, шаги без записи кэша
    (`not run` — ни разу не генерировались), шаги со `status: uncertain`
    (провал по недостатку данных должен всплывать, пока спек не поправят) и
    шаги с неразобранными конфликтами (каталог конфликтов ещё на месте).
    Шаг с разобранными конфликтами (каталог удалён) принимается, даже если
    его вход не менялся. Неприменённый сохранённый ответ принятого шага
    удаляется.
    Статус записи кэша (`confirmed`/`proposed`) сохраняется, у записи
    выставляется `accepted: true`.

    Шаги обходятся по порядку: принятый `constraints.yaml` — тот, что на
    диске, — сразу же входит в хеш `generators_and_script`/`solutions_draft`.
    Возвращает `(состояния, spec_error)` как `compute_step_statuses`.
    """
    return _walk_steps(
        problem_id,
        _accept_step,
        only_step=only_step,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
    )


def _walk_steps(
    problem_id: str,
    visit,
    *,
    only_step: Optional[str],
    prompts_dir: Path,
    problems_dir: Path,
    templates_dir: Path,
) -> tuple[list[StepStatus], Optional[str]]:
    """Общий обход шагов для `llm status`/`llm accept`: опциональные шаги,
    подхват `constraints.yaml`, хеши — как в `run_pipeline`;
    `visit(problem_id, step_name, entry, hashes, artifact_present, problems_dir)`
    решает, что сказать (и сделать) про шаг."""
    try:
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return [], str(exc)

    statuses: list[StepStatus] = []
    upstream_artifacts: dict[str, Any] = {}

    for step_name in STEP_ORDER if only_step is None else [only_step]:
        module = _STEP_MODULES[step_name]

        skip_detail = _optional_skip_detail(step_name, spec)
        if skip_detail is not None:
            statuses.append(StepStatus(step_name, STATUS_SKIPPED_OPTIONAL, skip_detail))
            continue

        _load_constraints_upstream(step_name, problem_id, spec, upstream_artifacts, problems_dir)
        primary_path = module.primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

        try:
            hashes = _compute_step_hashes(
                problem_id,
                step_name,
                spec,
                upstream_artifacts,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
            )
        except (ValueError, SpecReferenceError) as exc:
            statuses.append(StepStatus(step_name, STATUS_BLOCKED, str(exc)))
        else:
            cache_entry = load_cache_entry(problem_id, step_name, problems_dir=problems_dir)
            statuses.append(
                visit(
                    problem_id,
                    step_name,
                    cache_entry,
                    hashes,
                    _artifact_present(primary_path),
                    problems_dir,
                )
            )

        if step_name == "constraints_pick" and primary_path.exists():
            upstream_artifacts[CONSTRAINTS_ARTIFACT] = primary_path.read_text(encoding="utf-8")

    return statuses, None


def _status_of_step(
    problem_id: str,
    step_name: str,
    cache_entry: Optional[CacheEntry],
    hashes: _StepHashes,
    artifact_present: bool,
    problems_dir: Path,
) -> StepStatus:
    if cache_entry is not None and cache_entry.conflicts is not None:
        status = StepStatus(
            step_name,
            STATUS_CONFLICT,
            _conflicts_detail(problem_id, step_name, cache_entry, problems_dir),
        )
    else:
        status = _cache_status_of_step(step_name, cache_entry, hashes, artifact_present)
    if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
        hint = (
            "a saved model response is not applied — "
            f"'orchestrator {problem_address(problems_dir, problem_id)} "
            f"llm apply --step {step_name}'"
        )
        status.detail = f"{status.detail}; {hint}" if status.detail else hint
    return status


def _conflicts_resolved(problem_id: str, entry: CacheEntry, problems_dir: Path) -> bool:
    """Автор разобрал конфликты прошлого прогона — удалил их каталог."""
    assert entry.conflicts is not None
    return not (Path(problems_dir) / problem_id / entry.conflicts).exists()


def _conflicts_detail(
    problem_id: str, step_name: str, entry: CacheEntry, problems_dir: Path
) -> str:
    accept = (
        f"'orchestrator {problem_address(problems_dir, problem_id)} "
        f"llm accept --step {step_name}'"
    )
    if _conflicts_resolved(problem_id, entry, problems_dir):
        return f"conflicts of the last run resolved ({entry.conflicts} deleted) — run {accept}"
    return (
        f"unresolved conflicts of the last run in {entry.conflicts} — resolve by hand, "
        f"delete the directory and run {accept}"
    )


def _cache_status_of_step(
    step_name: str,
    cache_entry: Optional[CacheEntry],
    hashes: _StepHashes,
    artifact_present: bool,
) -> StepStatus:
    if cache_entry is None:
        return StepStatus(step_name, STATUS_NOT_RUN)
    if cache_entry.status == UNCERTAIN_STATUS:
        return StepStatus(
            step_name,
            STATUS_UNCERTAIN,
            "last run ended with status: uncertain — fix the spec and run again",
        )
    if not artifact_present:
        return StepStatus(step_name, STATUS_STALE, "artifact is missing on disk")
    if is_cache_valid(cache_entry, hashes.input_hash, hashes.prompt_hash, hashes.model):
        return StepStatus(step_name, STATUS_CACHE_HIT)
    return StepStatus(step_name, STATUS_STALE, _stale_reason(cache_entry, hashes))


def _accept_step(
    problem_id: str,
    step_name: str,
    cache_entry: Optional[CacheEntry],
    hashes: _StepHashes,
    artifact_present: bool,
    problems_dir: Path,
) -> StepStatus:
    current = _status_of_step(
        problem_id, step_name, cache_entry, hashes, artifact_present, problems_dir
    )
    if current.state == STATUS_CACHE_HIT:
        return current
    if current.state == STATUS_CONFLICT:
        assert cache_entry is not None
        if not _conflicts_resolved(problem_id, cache_entry, problems_dir):
            return StepStatus(step_name, STATUS_NOT_ACCEPTED, current.detail)
        if not artifact_present:
            return StepStatus(
                step_name, STATUS_NOT_ACCEPTED, "conflicts resolved, but the artifact is missing on disk"
            )
        accepted_detail = f"conflicts resolved ({cache_entry.conflicts} deleted)"
    elif current.state != STATUS_STALE or not artifact_present:
        detail = current.detail or "the step has never been generated"
        return StepStatus(
            step_name,
            STATUS_NOT_ACCEPTED,
            f"{current.state}: {detail} — run "
            f"'orchestrator {problem_address(problems_dir, problem_id)} llm generate'",
        )
    else:
        accepted_detail = f"was stale: {current.detail}"

    assert cache_entry is not None
    save_cache_entry(
        problem_id,
        step_name,
        CacheEntry(
            input_hash=hashes.input_hash,
            prompt_hash=hashes.prompt_hash,
            model=hashes.model,
            status=cache_entry.status,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            input_parts=hashes.input_parts,
            prompt_parts=hashes.prompt_parts,
            input_values=hashes.input_values,
            accepted=True,
        ),
        problems_dir=problems_dir,
    )
    # Принятые файлы актуальны — неприменённый ответ того же шага больше не нужен.
    discard_pending_response(problem_id, step_name, problems_dir=problems_dir)
    return StepStatus(step_name, STATUS_ACCEPTED, accepted_detail)
