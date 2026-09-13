"""Шаг 3 — генераторы (C++/testlib) и FreeMarker test-script, CLAUDE.md,
"Роль каждого генеративного шага", пункт 3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "generators_and_script"

# Имя артефакта шага constraints_pick, от которого зависит этот шаг
# (CLAUDE.md, "Кэширование по хешу спека": вход шага 3 — секция generation +
# содержимое outputs/<id>/constraints.yaml, полученное на шаге 2).
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# Optional[list[...]]-поля секции generation, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["generator_ideas", "base_template_refs"]

# Optional[list[...]]-поле секции solutions, которое используется этим шагом
# (не всей секцией solutions_draft, а только это поле — источник adversarial-
# тестов, см. CLAUDE.md, "Роль каждого генеративного шага", п.3).
_SOLUTIONS_LIST_FIELDS = ["known_wrong_approaches"]


def _solutions_section_data(spec: ProblemSpec) -> dict[str, Any]:
    """`solutions.known_wrong_approaches` — источник adversarial-тестов для
    user.md.j2 (см. CLAUDE.md, "Роль каждого генеративного шага", п.3). В
    отличие от `solutions_draft`, секция `solutions` здесь может отсутствовать
    целиком (она опциональна по SPEC_FORMAT.md) — в этом случае считаем
    `known_wrong_approaches` пустым списком, а не падаем и не пропускаем шаг:
    механические генераторы по `generator_ideas`/ограничениям всё равно
    должны быть выданы.
    """
    if spec.solutions is None:
        return {"known_wrong_approaches": []}
    return with_default_lists(
        spec.solutions.model_dump(mode="json"), _SOLUTIONS_LIST_FIELDS
    )


def _require_constraints_yaml(
    step_name: str, problem_id: str, upstream_artifacts: Optional[dict[str, Any]]
) -> str:
    """Общая проверка для `generators_and_script` и `solutions_draft` — оба
    зависят от `constraints.yaml`. `step_name` передаётся явно (а не берётся
    из модульной константы), чтобы сообщение об ошибке называло реальный шаг,
    вызвавший проверку, а не всегда `generators_and_script`.
    """
    if not upstream_artifacts or CONSTRAINTS_ARTIFACT not in upstream_artifacts:
        raise ValueError(
            f"Шаг '{step_name}' для '{problem_id}': в upstream_artifacts нет "
            f"'{CONSTRAINTS_ARTIFACT}' — сначала должен успешно отработать "
            "шаг 'constraints_pick' (CLAUDE.md, 'Роль каждого генеративного "
            "шага', п.3)"
        )
    return upstream_artifacts[CONSTRAINTS_ARTIFACT]


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". В отличие от шагов 1-2, здесь
    `upstream_artifacts["constraints.yaml"]` обязателен и входит в хеш —
    без него та же `ValueError`, что и в `run_step`. Помимо секции
    `generation`, в хеш также входит `solutions.known_wrong_approaches` (если
    секция `solutions` задана) — правка списка неверных подходов должна
    инвалидировать кэш этого шага, даже если секция `generation` не менялась
    (CLAUDE.md, "Кэширование по хешу спека").
    """
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    section_data = {
        "generation": with_default_lists(
            spec.generation.model_dump(mode="json"), _LIST_FIELDS
        ),
        "known_wrong_approaches": _solutions_section_data(spec)["known_wrong_approaches"],
    }
    return _cache_compute_input_hash(
        STEP_NAME, section_data, upstream_artifacts={CONSTRAINTS_ARTIFACT: constraints_yaml}
    )


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    """Путь к test-script'у — единственному детерминированному по имени
    артефакту шага (генераторы `.cpp` модель называет сама, а имя скрипта
    жёстко зависит от `generation.script_style`, см. CLAUDE.md, "Структура
    каталогов": `test_script` для `flat`, `test_script_groups` для `groups`).
    """
    script_name = "test_script" if spec.generation.script_style == "flat" else "test_script_groups"
    return Path(outputs_dir) / problem_id / script_name


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `generators_and_script` для `problem_id`.

    Вход: секция `generation` спека (форма входных данных, идеи генераторов,
    `reuse_existing_generators`/`base_template_refs`, `script_style`) плюс
    зафиксированные ограничения из шага `constraints_pick` — они передаются
    через `upstream_artifacts["constraints.yaml"]` (содержимое файла,
    записанного шагом 2) и подставляются в промпт как единственный источник
    диапазонов N/TL/ML и тестовых групп. Плюс, отдельно от `generation`,
    `solutions.known_wrong_approaches` (если секция `solutions` в спеке
    задана) — источник для adversarial-тестов под конкретные неверные
    решения (CLAUDE.md, "Роль каждого генеративного шага", п.3): этот шаг
    делает не только механическую реализацию уже заданных
    `generation.generator_ideas`, но и содержательный дизайн стресс-тестов,
    ловящих перечисленные неверные подходы, — с пометкой `status: proposed`
    и обоснованием в `notes`. Если секция `solutions` отсутствует или
    `known_wrong_approaches` пуст — это не блокирует шаг, промпт инструктирует
    модель явно отметить в `notes`, что adversarial-случаи не проектировались.

    Шагу запрещено придумывать собственные диапазоны переменных или тестовые
    группы — они уже зафиксированы шагом `constraints_pick`, этот шаг обязан
    их использовать как есть, не выводя ограничения заново из спека
    (CLAUDE.md, "Роль каждого генеративного шага", п.3: "FreeMarker-скрипт
    должен использовать зафиксированные ограничения... из шага 2"). Если для
    генерации не хватает данных (например, не описана форма входных данных
    для многомерной структуры) — `status: uncertain`, и вызов завершится
    `StepUncertainError` без записи файлов.

    При успехе пишет сгенерированные `.cpp`-файлы в
    `outputs/<problem_id>/generators/`, а `test_script`/`test_script_groups`
    — в `outputs/<problem_id>/` (см. CLAUDE.md, "Структура каталогов").

    `upstream_artifacts` обязателен и должен содержать ключ
    `"constraints.yaml"` с содержимым артефакта шага `constraints_pick` —
    без него шаг не может быть запущен (нарушится п.3 выше), поэтому
    отсутствие ключа — ошибка вызывающего кода (обычно пайплайна,
    запустившего шаги не по порядку), а не `StepUncertainError`.
    """
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)

    section_data = with_default_lists(
        spec.generation.model_dump(mode="json"), _LIST_FIELDS
    )
    context = {
        "problem_id": problem_id,
        "generation": section_data,
        "solutions": _solutions_section_data(spec),
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)

    def resolve_artifact_path(filename: str) -> Path:
        base = Path(outputs_dir) / problem_id
        if filename.endswith(".cpp"):
            return base / "generators" / filename
        return base / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        outputs_dir=outputs_dir,
        templates_dir=templates_dir,
    )
