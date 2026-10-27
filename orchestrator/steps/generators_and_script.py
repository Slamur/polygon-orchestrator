"""Шаг 3 — генераторы (C++/testlib) и FreeMarker test-script, CLAUDE.md,
"Роль каждого генеративного шага", пункт 3.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    directory_artifacts,
    existing_artifacts,
    artifact_filename,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)

STEP_NAME = "generators_and_script"

# Имя артефакта шага constraints_pick, от которого зависит этот шаг
# (CLAUDE.md, "Кэширование по хешу спека": вход шага 3 — секция generation +
# содержимое problems/<id>/constraints.yaml, полученное на шаге 2).
CONSTRAINTS_ARTIFACT = "constraints.yaml"

# Каталог генераторов внутри problems/<problem_id>/ — туда шаг кладёт `.cpp`.
GENERATORS_SUBDIR = "generators"

# Стандартные функции test-script'а (div, pow, fact, ...) — шаблон автора в
# templates/. Модель обязана копировать блок целиком (prompts/
# generators_and_script/system.md), но на практике выкидывает неиспользуемые
# функции, а автору они нужны под рукой при ручной правке скрипта, —
# поэтому выкинутые дописываются механически (`restore_script_functions`).
SCRIPT_FUNCTIONS_FILENAME = "test_script_functions.ftl"
SCRIPT_NAMES = ("test_script", "test_script_groups")
_SCRIPT_SECTION_MARKER = '<#assign desc = "{}"/>'
_FUNCTION_RE = re.compile(r"<#function\s+(\w+)\b.*?</#function>", re.S)

# Optional[list[...]]-поля секции generation, по которым user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["generator_ideas", "specific_test_ideas"]

# Optional[list[...]]-поле секции solutions, которое используется этим шагом
# (не всей секцией solutions_draft, а только это поле — источник adversarial-
# тестов, см. CLAUDE.md, "Роль каждого генеративного шага", п.3).
_SOLUTIONS_LIST_FIELDS = ["known_wrong_approaches"]


def _solutions_section_data(spec: ProblemSpec) -> dict[str, Any]:
    """Из секции `solutions` — то, что нужно для дизайна тестов (CLAUDE.md,
    "Роль каждого генеративного шага", п.3): `known_wrong_approaches` —
    источник adversarial-тестов, `algorithm_hint` — идея авторского решения,
    по которой модель может предложить свои тесты сверх заказанных автором
    (`kind: suggestion`). В отличие от `solutions_draft`, секция `solutions`
    здесь может отсутствовать целиком (она опциональна по SPEC_FORMAT.md) —
    в этом случае `known_wrong_approaches` — пустой список, `algorithm_hint`
    — `None`, а не падаем и не пропускаем шаг: механические генераторы по
    `generator_ideas`/ограничениям всё равно должны быть выданы. Остальные
    поля секции не возвращаются: они входят в хеш кэша
    (`_build_context_sections`), и правка, например, `languages` не должна
    инвалидировать генераторы.
    """
    if spec.solutions is None:
        return {"algorithm_hint": None, "known_wrong_approaches": []}
    section_data = with_default_lists(
        spec.solutions.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _SOLUTIONS_LIST_FIELDS
    )
    return {
        "algorithm_hint": section_data["algorithm_hint"],
        "known_wrong_approaches": section_data["known_wrong_approaches"],
    }


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """Секции спека для рендера промпта И для входа хеша кэша — единая точка
    сборки, чтобы они не могли разойтись (CLAUDE.md, "Кэширование по хешу
    спека"): секция `generation`, `solutions.known_wrong_approaches` и
    `solutions.algorithm_hint` (см. `_solutions_section_data`), формат ввода
    из `statement_draft` (`formal_input_sketch`, `indexing`, input примеров —
    `base.statement_io_section`) и легенда (`legend_sketch`) — без сути
    задачи модель не может предложить свои тесты под вероятные ошибки.
    Отдельного поля формы входа в `generation` нет, чтобы не дублировать её
    вручную в двух местах спека.
    """
    statement = statement_io_section(spec, include_output_and_legend=False)
    statement["legend_sketch"] = spec.statement_draft.legend_sketch
    return {
        "generation": with_default_lists(
            spec.generation.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD), _LIST_FIELDS
        ),
        "solutions": _solutions_section_data(spec),
        "statement": statement,
    }


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
            f"Step '{step_name}' for '{problem_id}': upstream_artifacts has no "
            f"'{CONSTRAINTS_ARTIFACT}' — the 'constraints_pick' step must "
            "succeed first (CLAUDE.md, generative step roles, item 3)"
        )
    return upstream_artifacts[CONSTRAINTS_ARTIFACT]


def _script_functions(script: str) -> dict[str, str]:
    """Имя функции -> её определение `<#function ...>...</#function>`."""
    return {m.group(1): m.group(0) for m in _FUNCTION_RE.finditer(script)}


def restore_script_functions(script: str, functions_block: str) -> tuple[str, list[str]]:
    """Дописывает в `script` функции из `functions_block`, которых в нём нет
    (по имени; функцию, которую модель переписала, не трогает). Куда:
    - после маркера секции `<#assign desc = "Functions"/>`, если он есть;
    - иначе перед первой функцией скрипта, если она есть;
    - иначе весь блок с маркером — перед секцией "Constraints", а без неё —
      в начало скрипта (определения функций в FreeMarker видны во всём
      шаблоне, так что порядок на работу скрипта не влияет).
    Возвращает новый скрипт и имена дописанных функций (в порядке блока).
    """
    present = _script_functions(script)
    missing = [(name, body) for name, body in _script_functions(functions_block).items()
               if name not in present]
    if not missing:
        return script, []
    names = [name for name, _ in missing]
    bodies = "\n".join(body for _, body in missing) + "\n"

    functions_marker = _SCRIPT_SECTION_MARKER.format("Functions")
    marker_at = script.find(functions_marker)
    if marker_at != -1:
        insert_at = marker_at + len(functions_marker)
        return script[:insert_at] + "\n" + bodies.rstrip("\n") + script[insert_at:], names
    first_function = _FUNCTION_RE.search(script)
    if first_function:
        insert_at = first_function.start()
        return script[:insert_at] + bodies + script[insert_at:], names
    block = functions_marker + "\n" + bodies + "\n"
    constraints_at = script.find(_SCRIPT_SECTION_MARKER.format("Constraints"))
    insert_at = 0 if constraints_at == -1 else constraints_at
    return script[:insert_at] + block + script[insert_at:], names


# Директива FreeMarker, занимающая хвост строки: `<#if value >= n>`. Внутри
# тега `>` закрывает тег (`<#if value>` + текст `= n>`), поэтому сравнения
# пишутся словами (`gt`/`gte`/`lt`/`lte`, templates/tutorials/freemarker.md),
# но модель время от времени пишет знаки. Границы выражения берутся по концу
# строки, а не по первому `>`, — иначе его не восстановить.
_DIRECTIVE_LINE_RE = re.compile(
    r"(<#(?:if|elseif|assign|local|global|return)\s)((?:(?!<[#/@]).)*?)(\s*/?>[ \t]*)$", re.M
)
_STRING_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
# `->` (лямбда) и `..<` (диапазон) — не сравнения.
_COMPARISON_RE = re.compile(r"\s*(?<![-.])(>=|<=|>|<)\s*")
_COMPARISON_WORDS = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}


def _replace_comparisons(expression: str) -> tuple[str, int]:
    parts: list[str] = []
    count = 0
    position = 0
    for string in [*_STRING_RE.finditer(expression), None]:
        end = string.start() if string else len(expression)
        code, replaced = _COMPARISON_RE.subn(
            lambda m: f" {_COMPARISON_WORDS[m.group(1)]} ", expression[position:end]
        )
        parts.append(code)
        count += replaced
        if string:
            parts.append(string.group(0))
            position = string.end()
    return "".join(parts), count


def fix_script_comparisons(script: str) -> tuple[str, int]:
    """Заменяет знаки сравнения в директивах скрипта на `gt`/`gte`/`lt`/`lte`
    (см. `_DIRECTIVE_LINE_RE`). Только директивы, которые заканчиваются в
    конце своей строки; строковые литералы не трогает. Возвращает новый
    скрипт и число замен."""
    total = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal total
        expression, count = _replace_comparisons(match.group(2))
        total += count
        return match.group(1) + expression + match.group(3)

    return _DIRECTIVE_LINE_RE.sub(replace, script), total


def _restore_functions_in_artifacts(
    functions_block: str,
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """`postprocess_artifacts` для `run_generative_step`: прогоняет каждый
    test-script ответа через `fix_script_comparisons` и
    `restore_script_functions` и оставляет заметку по каждому, где что-то
    исправлено или дописано."""

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        result = dict(artifacts)
        notes: list[dict[str, Any]] = []
        for name in SCRIPT_NAMES:
            if name not in result:
                continue
            result[name], fixed = fix_script_comparisons(result[name])
            if fixed:
                notes.append({
                    "field": name,
                    "kind": "restored",
                    "explanation": (
                        f"знаки сравнения внутри директив FreeMarker ({fixed} шт.) заменены "
                        "оркестратором на gt/gte/lt/lte: `>` внутри тега закрывает тег"
                    ),
                })
            result[name], restored = restore_script_functions(result[name], functions_block)
            if restored:
                notes.append({
                    "field": name,
                    "kind": "restored",
                    "explanation": (
                        f"модель не скопировала стандартные функции {', '.join(restored)} "
                        f"из {SCRIPT_FUNCTIONS_FILENAME} — дописаны оркестратором"
                    ),
                })
        return result, notes

    return postprocess


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". В отличие от шагов 1-2, здесь
    `upstream_artifacts["constraints.yaml"]` обязателен и входит в хеш —
    без него та же `ValueError`, что и в `run_step`. Помимо секции
    `generation`, в хеш также входят (см. `_build_context_sections`):
    - формат ввода из `statement_draft` (`formal_input_sketch`, `indexing`,
      input примеров) — правка формата входа в условии должна
      инвалидировать кэш генераторов, даже если секция `generation` не
      менялась;
    - `solutions.known_wrong_approaches` (если секция `solutions` задана) —
      правка списка неверных подходов должна инвалидировать кэш этого шага,
      даже если секция `generation` не менялась (CLAUDE.md, "Кэширование по
      хешу спека");
    - легенда и `solutions.algorithm_hint` — по ним модель предлагает свои
      тесты сверх заказанных автором.
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """Что входит в хеш входа шага: `(секция спека, апстрим-артефакты)` —
    общий источник для `compute_input_hash` и хешей по частям
    (`cache.compute_input_values`), чтобы они не разошлись."""
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    return _build_context_sections(spec), {CONSTRAINTS_ARTIFACT: constraints_yaml}


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы для конкретного вызова — файлы, которые этому шагу дают
    референсы спека (общие `references` и `generation.references`, см.
    `orchestrator/references.py`). Единая сигнатура со всеми шагами нужна
    `pipeline.py`, чтобы считать `compute_prompt_hash` одинаково.
    """
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Путь к test-script'у — единственному детерминированному по имени
    артефакту шага (генераторы `.cpp` модель называет сама, а имя скрипта
    жёстко зависит от `generation.script_style`, см. CLAUDE.md, "Структура
    каталогов": `test_script` для `flat`, `test_script_groups` для `groups`).
    """
    script_name = "test_script" if spec.generation.script_style == "flat" else "test_script_groups"
    return Path(problems_dir) / problem_id / script_name


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`).
    `generators/*.cpp` (под именами файлов — так их раскладывает
    `run_step`) и оба test-script'а, какие есть."""
    problem_dir = Path(problems_dir) / problem_id
    return {
        **directory_artifacts(problem_dir / GENERATORS_SUBDIR, "*.cpp"),
        **existing_artifacts({name: problem_dir / name for name in SCRIPT_NAMES}),
    }


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    revision: Optional[StepRevision] = None,
    saved_response: Optional[ModelResponse] = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `generators_and_script` для `problem_id`.

    Вход: секция `generation` спека (идеи генераторов, `script_style`) плюс
    формат ввода из `statement_draft` (`formal_input_sketch`, `indexing`,
    input примеров — а не отдельное поле `generation`, см.
    `_build_context_sections`), плюс
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
    `problems/<problem_id>/generators/`, а `test_script`/`test_script_groups`
    — в `problems/<problem_id>/` (см. CLAUDE.md, "Структура каталогов").

    `upstream_artifacts` обязателен и должен содержать ключ
    `"constraints.yaml"` с содержимым артефакта шага `constraints_pick` —
    без него шаг не может быть запущен (нарушится п.3 выше), поэтому
    отсутствие ключа — ошибка вызывающего кода (обычно пайплайна,
    запустившего шаги не по порядку), а не `StepUncertainError`.
    """
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)

    context = {
        "problem_id": spec.problem_id,
        **_build_context_sections(spec),
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        base = Path(problems_dir) / problem_id
        if filename.endswith(".cpp"):
            return base / GENERATORS_SUBDIR / artifact_filename(filename, GENERATORS_SUBDIR)
        return base / artifact_filename(filename)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        for filename in artifacts:
            resolve_artifact_path(filename)

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        # Файлы независимы: генераторы/скрипт и решения применяются по одному.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=_restore_functions_in_artifacts(
            (Path(templates_dir) / SCRIPT_FUNCTIONS_FILENAME).read_text(
                encoding="utf-8"
            )
        ),
    )
