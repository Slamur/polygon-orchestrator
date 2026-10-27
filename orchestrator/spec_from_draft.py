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

Это не генеративный шаг пайплайна: без кэша, режима доработки и
`llm apply` — спек создаётся один раз, существующий не перезаписывается.
Ответ модели пишется в `spec.yaml` в любом случае, если в нём есть спек
(даже не проходящий валидацию или при `status: uncertain` — это черновик,
который автор всё равно дочитывает), чтобы вызов не пропадал. `problem_id`
и референс на задачу-основу оркестратор ставит сам, поверх ответа модели.
Копия черновика ложится рядом со спеком, в `authoring/` каталога задачи
(`drafts.problem_draft_name`).
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
    find_authoring_file,
    problem_address,
    problem_paths,
)
from orchestrator.spec import (
    SPEC_TEMPLATE,
    SpecInit,
    SpecInitError,
    add_reference,
    set_problem_id,
    spec_validation_errors,
    write_spec,
)
from orchestrator.steps.base import (
    PROMPTS_DIR,
    TEMPLATES_DIR,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_UNCERTAIN,
    read_system_prompt,
    render_user_prompt,
)

STEP_NAME = "spec_from_draft"
SPEC_ARTIFACT = SPEC_FILENAME

# Формат спека для модели — относительно каталога, в котором лежит
# `templates/` (корень репозитория).
SPEC_FORMAT_DOC = Path("docs") / "SPEC_FORMAT.md"

_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}


@dataclass
class SpecFromDraftResult:
    """Итог `init --draft`. `path` — созданный спек, `None` — модель спека не
    вернула и ничего не записано. `validation_errors` — ошибки валидации
    созданного спека (`spec.load_spec`); пустой список — спек валиден."""

    status: str
    notes: list[dict[str, Any]] = field(default_factory=list)
    path: Optional[Path] = None
    draft_copy: Optional[Path] = None
    validation_errors: list[str] = field(default_factory=list)


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
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> SpecFromDraftResult:
    """Создаёт спек `init.path` по черновику `draft_path` (см. докстринг
    модуля). `init` — уже проверенное место и основа спека
    (`spec.prepare_spec_init`), `draft_path` — непустой черновик
    (`drafts.find_draft`)."""
    draft_text = Path(draft_path).read_text(encoding="utf-8")

    system_prompt = read_system_prompt(STEP_NAME, prompts_dir=prompts_dir)
    user_prompt = render_user_prompt(
        STEP_NAME,
        {
            "problem_id": init.problem_id,
            "draft": draft_text.rstrip("\n"),
            "base_spec": init.base_text.rstrip("\n"),
            "reference": init.reference,
        },
        prompts_dir=prompts_dir,
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
    if spec_text is None or not spec_text.strip():
        if response.status != STATUS_UNCERTAIN:
            notes.append(_note(SPEC_ARTIFACT, "uncertain", "the model returned no spec"))
        return result

    spec_text, finalize_notes = _finalize_spec_text(init, spec_text)
    notes.extend(finalize_notes)
    result.path = write_spec(init, spec_text)
    result.draft_copy = init.path.parent / problem_draft_name(draft_path)
    shutil.copyfile(draft_path, result.draft_copy)
    result.validation_errors = spec_validation_errors(result.path, expected_id=init.problem_id)
    return result
