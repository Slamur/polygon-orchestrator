"""Спек задачи по черновику автора — `orchestrator <задача> init --draft NAME`
(CLAUDE.md, "Запуск").

Черновик (`orchestrator/drafts.py`) — описание задачи в свободной форме.
Модель (`STEP_NAME` в `model_router.py`) переносит его в спек по формату
`docs/SPEC_FORMAT.md`: то, что написал автор, сохраняет, а недостающее
дозаполняет сама и может добавить свои идеи (решения, ошибки участников,
тесты) — каждое такое значение помечено в спеке комментарием
`# PROPOSED, REVIEW ME: ...` или `# SUGGESTION: ...` и заметкой в ответе
(`prompts/spec_from_draft/system.md`). Основа спека — шаблон
`templates/spec_template.yaml` или, с `--ref`, спек задачи-основы
(`spec.prepare_spec_init`): тогда черновик описывает, чем новая задача
отличается от неё.

Это не генеративный шаг пайплайна: без кэша и `llm apply`. Ответ модели
пишется в `spec.yaml` в любом случае, если в нём есть спек (даже не
проходящий валидацию или при `status: uncertain` — это черновик, который
автор всё равно дочитывает), чтобы вызов не пропадал. `problem_id` и
референс на задачу-основу оркестратор ставит сам, поверх ответа модели.
Копия черновика ложится рядом со спеком, в `authoring/` каталога задачи
(`drafts.problem_draft_name`).

Если спек у задачи уже есть, `init --draft` его дорабатывает, как
`llm generate` — файлы шага (`prepare_spec_revision`): модель получает
текущий спек (возможно, с ручными правками автора) и что изменилось в
черновике — прошлая копия черновика в `authoring/` против нового — общим
блоком `prompts/revise.md.j2`. Черновик не изменился — модель не
вызывается (`draft_unchanged`). Прежние версии спека и копии черновика —
в `.llm_backup/<время>/`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from orchestrator import model_router
from orchestrator.drafts import problem_draft_name
from orchestrator.problem_paths import (
    EXAMPLES_DIR,
    SPEC_FILENAME,
    authoring_dir,
    find_authoring_file,
    is_draft_name,
    problem_address,
    problem_id_for,
    problem_paths,
)
from orchestrator.spec import (
    SPEC_TEMPLATE,
    SpecInit,
    SpecInitError,
    add_reference,
    set_problem_id,
    spec_path,
    spec_validation_errors,
    write_spec,
)
from orchestrator.steps.base import (
    PROMPTS_DIR,
    TEMPLATES_DIR,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_UNCERTAIN,
    Backup,
    StepRevision,
    read_system_prompt,
    render_revision_prompt,
    render_user_prompt,
)

STEP_NAME = "spec_from_draft"
SPEC_ARTIFACT = SPEC_FILENAME

# Формат спека для модели — относительно каталога, в котором лежит
# `templates/` (корень репозитория).
SPEC_FORMAT_DOC = Path("docs") / "SPEC_FORMAT.md"

_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}

# Под каким именем черновик — часть входа в блоке "что изменилось"
# (`prompts/revise.md.j2`).
DRAFT_INPUT = "draft"


@dataclass
class SpecFromDraftResult:
    """Итог `init --draft`. `path` — созданный или доработанный спек, `None`
    — модель спека не вернула и ничего не записано. `unchanged` — при
    доработке модель оставила спек как есть (`path` — он же, не
    перезаписан). `validation_errors` — ошибки валидации спека
    (`spec.load_spec`); пустой список — спек валиден. `backup_dir` —
    `.llm_backup/<время>/` с прежними версиями спека и копии черновика."""

    status: str
    notes: list[dict[str, Any]] = field(default_factory=list)
    path: Optional[Path] = None
    draft_copy: Optional[Path] = None
    unchanged: bool = False
    validation_errors: list[str] = field(default_factory=list)
    backup_dir: Optional[Path] = None


@dataclass
class DraftRevision:
    """Доработка существующего спека по изменённому черновику.

    `previous_draft` — текст прошлой копии черновика из `authoring/`, по
    которому писался спек; `None` — копии нет (или их несколько), что
    изменилось — неизвестно. `previous_copies` — эти копии: при записи
    заменяются копией нового черновика (расширение может быть другим)."""

    previous_draft: Optional[str]
    previous_copies: list[Path] = field(default_factory=list)


def prepare_spec_revision(
    problem_path: str, *, problems_dir: Path
) -> tuple[SpecInit, DraftRevision]:
    """Основа доработки спека задачи `problems_dir/<problem_path>/`: текущий
    спек (`SpecInit.base_text`, без задачи-основы — референсы уже в нём) и
    прошлая копия черновика. Спека нет — `SpecInitError`."""
    path = spec_path(problem_path, problems_dir=problems_dir)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecInitError(f"failed to read {path}: {exc}") from exc
    directory = authoring_dir(Path(problems_dir) / problem_path)
    copies = sorted(p for p in directory.iterdir() if p.is_file() and is_draft_name(p.name))
    previous = copies[0].read_text(encoding="utf-8") if len(copies) == 1 else None
    init = SpecInit(
        problem_id=problem_id_for(problems_dir, problem_path), path=path, base_text=current
    )
    return init, DraftRevision(previous_draft=previous, previous_copies=copies)


def draft_unchanged(revision: DraftRevision, draft_path: Path) -> bool:
    """Черновик совпадает с прошлой копией, по которой писался спек."""
    return revision.previous_draft == Path(draft_path).read_text(encoding="utf-8")


def context_documents(
    *,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
    skip_reference: Optional[str] = None,
) -> dict[str, str]:
    """Документы для модели: формат спека (`SPEC_FORMAT_DOC`), шаблон
    (`SPEC_TEMPLATE`) и заполненные спеки учебных задач — образцы того, как
    выглядит готовый спек. Спек задачи-основы `skip_reference` уже есть в
    user-промпте и второй раз не прикладывается."""
    templates_dir = Path(templates_dir)
    documents = {
        SPEC_FORMAT_DOC.as_posix(): (templates_dir.parent / SPEC_FORMAT_DOC).read_text(
            encoding="utf-8"
        ),
        SPEC_TEMPLATE: (templates_dir / SPEC_TEMPLATE).read_text(encoding="utf-8"),
    }
    for example in problem_paths(examples_dir):
        address = problem_address(examples_dir, example)
        path = find_authoring_file(Path(examples_dir) / example, SPEC_FILENAME)
        if address != skip_reference and path.is_file():
            documents[f"{address}/{SPEC_FILENAME}"] = path.read_text(encoding="utf-8")
    return documents


def _note(field_name: str, kind: str, explanation: str) -> dict[str, Any]:
    return {"field": field_name, "kind": kind, "explanation": explanation}


def _finalize_spec_text(init: SpecInit, text: str) -> tuple[str, list[dict[str, Any]]]:
    """`problem_id` и референс на задачу-основу — поверх ответа модели: их
    ставит оркестратор, а не модель. Не получилось (например, ответ — не
    YAML) — текст как есть и заметка."""
    notes: list[dict[str, Any]] = []
    try:
        text = set_problem_id(text, init.problem_id)
    except SpecInitError as exc:
        notes.append(_note("problem_id", "uncertain", f"not set by the orchestrator: {exc}"))
    if init.reference is not None:
        try:
            text = add_reference(text, init.reference)
        except SpecInitError as exc:
            notes.append(
                _note("references", "uncertain", f"'{init.reference}' not added: {exc}")
            )
    if not text.endswith("\n"):
        text += "\n"
    return text, notes


def generate_spec_from_draft(
    init: SpecInit,
    draft_path: Path,
    *,
    revision: Optional[DraftRevision] = None,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> SpecFromDraftResult:
    """Создаёт спек `init.path` по черновику `draft_path` (см. докстринг
    модуля). `init` — уже проверенное место и основа спека
    (`spec.prepare_spec_init`), `draft_path` — непустой черновик
    (`drafts.find_draft`). С `revision` — дорабатывает существующий спек
    `init.path` (`prepare_spec_revision`)."""
    draft_text = Path(draft_path).read_text(encoding="utf-8")

    system_prompt = read_system_prompt(STEP_NAME, prompts_dir=prompts_dir)
    user_prompt = render_user_prompt(
        STEP_NAME,
        {
            "problem_id": init.problem_id,
            "draft": draft_text.rstrip("\n"),
            "base_spec": init.base_text.rstrip("\n"),
            "reference": init.reference,
            "revision": revision is not None,
        },
        prompts_dir=prompts_dir,
    )
    if revision is not None:
        step_revision = StepRevision(
            current_paths={SPEC_ARTIFACT: init.path},
            previous_input_values=None
            if revision.previous_draft is None
            else {DRAFT_INPUT: revision.previous_draft.rstrip("\n")},
        )
        user_prompt = user_prompt.rstrip("\n") + "\n\n" + render_revision_prompt(
            step_revision, {DRAFT_INPUT: draft_text.rstrip("\n")}, prompts_dir=prompts_dir
        )
    response = model_router.call_model(
        STEP_NAME,
        system_prompt,
        user_prompt,
        context_documents=context_documents(
            templates_dir=templates_dir, examples_dir=examples_dir, skip_reference=init.reference
        ),
    )
    if response.status not in _VALID_STATUSES:
        raise ValueError(
            f"'{STEP_NAME}': the model returned an unknown status "
            f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
        )

    notes = list(response.notes)
    result = SpecFromDraftResult(status=response.status, notes=notes)
    spec_text = response.artifacts.get(SPEC_ARTIFACT)
    for filename in sorted(set(response.artifacts) - {SPEC_ARTIFACT}):
        notes.append(_note(filename, "uncertain", "unexpected file in the response, ignored"))
    if revision is not None:
        for entry in [*response.removed, *response.renamed]:
            notes.append(
                _note(SPEC_ARTIFACT, "uncertain", f"ignored removal/rename of the spec: {entry}")
            )
    returned = spec_text is not None and bool(spec_text.strip())
    result.unchanged = (
        revision is not None and not returned and SPEC_ARTIFACT in response.unchanged
    )
    if not returned and not result.unchanged:
        if response.status != STATUS_UNCERTAIN:
            kept = ", kept as is" if revision is not None else ""
            notes.append(_note(SPEC_ARTIFACT, "uncertain", f"the model returned no spec{kept}"))
        return result

    backup = Backup(init.path.parent.parent)
    if not result.unchanged:
        spec_text, finalize_notes = _finalize_spec_text(init, spec_text)
        notes.extend(finalize_notes)
        if init.path.is_file() and init.path.read_text(encoding="utf-8") != spec_text:
            backup.save(init.path)
        write_spec(init, spec_text)
    result.path = init.path
    result.draft_copy = init.path.parent / problem_draft_name(draft_path)
    for copy in [] if revision is None else revision.previous_copies:
        if copy != result.draft_copy or copy.read_text(encoding="utf-8") != draft_text:
            backup.save(copy)
        if copy != result.draft_copy:
            copy.unlink()
    shutil.copyfile(draft_path, result.draft_copy)
    result.backup_dir = backup.directory
    result.validation_errors = spec_validation_errors(result.path, expected_id=init.problem_id)
    return result
