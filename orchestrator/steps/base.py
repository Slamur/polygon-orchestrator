"""Общий цикл одного генеративного шага.

Каждый из пяти шагов (`statement_draft`, `constraints_pick`,
`generators_and_script`, `solutions_draft`, `checker_draft`, см. CLAUDE.md,
"Роль каждого генеративного шага") — это одна и та же последовательность
действий над разными данными:

1. отрендерить `prompts/<step>/user.md.j2` (Jinja2) из секции спека
   (+ апстрим-артефактов, если шаг от них зависит);
2. прочитать `prompts/<step>/system.md` как системный промпт;
3. вызвать `model_router.call_model(...)`;
4. разобрать `ModelResponse.status` по "Правилу эскалации при неуверенности"
   (CLAUDE.md): `uncertain` — остановить шаг и не писать артефакты;
   `confirmed`/`proposed` — записать артефакты в `outputs/<problem_id>/...`,
   прогнать `compile_check` по всем `.cpp`-артефактам и сохранить
   `CacheEntry` через `cache.py`.

Этот модуль реализует шаги 1-5 один раз (`run_generative_step`); конкретные
модули в `orchestrator/steps/*.py` только знают, какую секцию спека читать,
как разложить артефакты по каталогам `outputs/<id>/...` и что входит в
апстрим-хеш — сами не дублируют этот цикл.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jinja2

from orchestrator import model_router
from orchestrator.cache import CacheEntry, compute_prompt_hash, save_cache_entry
from orchestrator.checks.compile_check import CompileResult, compile_check

PROMPTS_DIR = Path("prompts")
OUTPUTS_DIR = Path("outputs")
TEMPLATES_DIR = Path("templates")

# Раскладка templates_dir: `common/` — общее для всех задач (правила
# `tutorials/*.md`, `problem_lib.h`, `testlib.h`; `common/` же — include-каталог
# для compile-check), `examples/<problem_id>/` — учебные задачи-образцы, каждая
# в раскладке `outputs/<problem_id>/` плюс свой спек `spec.yaml`.
COMMON_SUBDIR = "common"
EXAMPLES_SUBDIR = "examples"
# Учебная задача, образцы которой прикладываются ко всем шагам. Выбора образцов
# по спеку пока нет — до тех пор пример один и общий для всех задач.
DEFAULT_EXAMPLE_SUBDIR = f"{EXAMPLES_SUBDIR}/array--prefix-sums"
EXAMPLE_SPEC_FILENAME = "spec.yaml"

_COMMON = COMMON_SUBDIR
_EXAMPLE = DEFAULT_EXAMPLE_SUBDIR

# Обязательные документы контекста на шаг (пути относительно templates_dir),
# см. CLAUDE.md, "Как использовать приложенные материалы": tutorials/*.md —
# это правила, а не справочный материал, а problem_lib.h/generators/*.cpp/
# test_script — основа, которую нужно расширять. Промпты (prompts/<step>/
# system.md) лишь ссылаются на эти файлы по имени — здесь их содержимое
# реально прикладывается к вызову модели (см. `call_model` в
# `model_router.py` и `LLMClient.generate`), а не пересказывается.
#
# Запись с `*` — glob-шаблон относительно templates_dir: прикладываются все
# подходящие файлы (в отсортированном порядке), так что новый пример,
# положенный в каталог, подхватывается без правки этого списка. `spec.yaml`
# учебной задачи — спек, для которого написаны все её файлы-образцы: вместе
# с ними он даёт модели пару "спек -> ожидаемый результат". Прикладывается ко
# всем шагам, у которых есть образец результата (у `statement_draft` такого
# образца нет). `constraints.yaml` — образец результата `constraints_pick`
# для того же учебного спека; шагу `generators_and_script` он нужен как
# образец его входа (по нему написан `test_script`). Решения учебной задачи
# (`solutions/*`) здесь не перечислены — они зависят от
# `solutions.languages` конкретного спека, см.
# `solutions_draft.extra_context_documents`.
STEP_CONTEXT_DOCUMENTS: dict[str, list[str]] = {
    "statement_draft": [
        f"{_COMMON}/tutorials/requirements.md",
        f"{_COMMON}/tutorials/polygon.md",
    ],
    "constraints_pick": [
        f"{_COMMON}/tutorials/requirements.md",
        f"{_EXAMPLE}/{EXAMPLE_SPEC_FILENAME}",
        f"{_EXAMPLE}/constraints.yaml",
        f"{_EXAMPLE}/validator.cpp",
        f"{_COMMON}/problem_lib.h",
    ],
    "generators_and_script": [
        f"{_COMMON}/tutorials/requirements.md",
        f"{_COMMON}/tutorials/freemarker.md",
        f"{_EXAMPLE}/{EXAMPLE_SPEC_FILENAME}",
        f"{_EXAMPLE}/constraints.yaml",
        f"{_COMMON}/problem_lib.h",
        f"{_EXAMPLE}/generators/*.cpp",
        f"{_EXAMPLE}/test_script",
    ],
    "solutions_draft": [
        f"{_COMMON}/tutorials/requirements.md",
        f"{_COMMON}/tutorials/polygon.md",
        f"{_EXAMPLE}/{EXAMPLE_SPEC_FILENAME}",
    ],
    "checker_draft": [
        f"{_COMMON}/tutorials/requirements.md",
        f"{_EXAMPLE}/{EXAMPLE_SPEC_FILENAME}",
        f"{_EXAMPLE}/checker.cpp",
        f"{_COMMON}/problem_lib.h",
    ],
}

# Статусы ответа модели по контракту из docs/PROMPTS.md.
STATUS_CONFIRMED = "confirmed"
STATUS_PROPOSED = "proposed"
STATUS_UNCERTAIN = "uncertain"
_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}


class StepUncertainError(Exception):
    """Шаг остановлен из-за `status: uncertain` в ответе модели.

    Реализует "Правило эскалации при неуверенности" (CLAUDE.md): если для
    ответа не хватает данных, явно заданных автором в спеке, модель обязана
    вернуть `uncertain` вместо того, чтобы додумывать значение, а оркестратор
    обязан остановить пайплайн для этого `problem_id` и не трогать
    `outputs/` — этот класс исключения и есть тот механический стоп-сигнал.
    """

    def __init__(self, step_name: str, problem_id: str, notes: list[dict[str, Any]]):
        self.step_name = step_name
        self.problem_id = problem_id
        self.notes = notes
        details = "; ".join(
            f"{note.get('field', '?')}: {note.get('explanation', '')}" for note in notes
        ) or "the model did not provide notes with the missing fields"
        super().__init__(
            f"Step '{step_name}' for '{problem_id}' stopped (status: uncertain): "
            f"{details}. Fix specs/{problem_id}.yaml and run the step again."
        )


@dataclass
class StepResult:
    """Результат успешного (`confirmed`/`proposed`) прогона шага.

    `notes` сохраняет заметки модели как есть (в т.ч. `kind: proposed`) —
    какие поля отмечать как "требует ручной проверки автором" в
    `outputs/` решает вызывающий код (пайплайн), см. CLAUDE.md, "Правило
    эскалации при неуверенности".
    """

    step_name: str
    problem_id: str
    status: str
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    compile_results: dict[str, CompileResult] = field(default_factory=dict)


def _jinja_env(prompts_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(prompts_dir)),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def with_default_lists(data: dict[str, Any], list_fields: list[str]) -> dict[str, Any]:
    """Копия `data`, где `None` по ключам из `list_fields` заменён на `[]`.

    В `docs/SPEC_FORMAT.md` часть полей спека — `Optional[list[...]] = None`
    (например, `known_ambiguities`, `special_guarantees`) специально
    допускает `null` как "автор ничего не указал". `user.md.j2`-шаблоны
    (см. `docs/PROMPTS.md`) перебирают такие поля через `{% for %}` без
    отдельной проверки на `null` — с `None` вместо `[]` рендеринг упадёт с
    `TypeError`, поэтому нормализация нужна до вызова Jinja, а не в шаблоне.
    """
    result = dict(data)
    for key in list_fields:
        if result.get(key) is None:
            result[key] = []
    return result


def statement_io_section(spec: Any, *, include_output_and_legend: bool) -> dict[str, Any]:
    """Формат ввода-вывода задачи из секции `statement_draft` спека — для
    шагов, которые сами по себе от этой секции не зависят, но без неё
    вынуждены угадывать формат (валидатор, генераторы, решения).

    Берётся именно спек, а не сгенерированные `outputs/<id>/statement/*.tex`:
    спек — первоисточник, и он есть, даже если `statement_draft` ушёл в
    `uncertain`. Возвращаемый словарь — одновременно контекст для
    `user.md.j2` и часть входа хеша кэша шага (единая точка сборки, чтобы они
    не разошлись).

    `include_output_and_legend=False` — только ввод (`formal_input_sketch`,
    `indexing`, input примеров): для `constraints_pick` легенда не
    передаётся намеренно — асимптотику из неё выводить запрещено, и лишний
    повод так сделать в промпте не нужен. `True` — дополнительно
    `formal_output_sketch`, `legend_sketch` и output примеров (`null`, если
    автор его не указал) — для решений, которым нужно и читать вход, и
    печатать ответ.
    """
    statement = spec.statement_draft
    section: dict[str, Any] = {
        "formal_input_sketch": statement.formal_input_sketch,
        "indexing": statement.indexing,
    }
    if include_output_and_legend:
        section["legend_sketch"] = statement.legend_sketch
        section["formal_output_sketch"] = statement.formal_output_sketch
        section["sample_examples"] = [
            {"input": ex.input, "output": ex.output} for ex in statement.sample_examples
        ]
    else:
        section["sample_examples"] = [{"input": ex.input} for ex in statement.sample_examples]
    return section


def render_user_prompt(
    step_name: str, context: dict[str, Any], *, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """Рендерит `prompts/<step_name>/user.md.j2` из `context`."""
    env = _jinja_env(prompts_dir)
    template = env.get_template(f"{step_name}/user.md.j2")
    return template.render(**context)


def read_system_prompt(step_name: str, *, prompts_dir: Path = PROMPTS_DIR) -> str:
    """Читает `prompts/<step_name>/system.md` как системный промпт."""
    return (Path(prompts_dir) / step_name / "system.md").read_text(encoding="utf-8")


def _expand_context_entry(step_name: str, entry: str, templates_dir: Path) -> list[str]:
    """Раскрывает запись `STEP_CONTEXT_DOCUMENTS` в список путей относительно
    `templates_dir`: обычный путь — как есть, glob-запись (с `*`) — во все
    подходящие файлы в отсортированном порядке (порядок входит в
    `compute_prompt_hash`, поэтому он должен быть детерминированным).
    """
    if "*" not in entry:
        if not (templates_dir / entry).exists():
            raise FileNotFoundError(
                f"Step '{step_name}': context document '{entry}' from "
                f"STEP_CONTEXT_DOCUMENTS not found at {templates_dir / entry}"
            )
        return [entry]

    matches = sorted(
        path.relative_to(templates_dir).as_posix()
        for path in templates_dir.glob(entry)
        if path.is_file()
    )
    if not matches:
        raise FileNotFoundError(
            f"Step '{step_name}': context document pattern '{entry}' from "
            f"STEP_CONTEXT_DOCUMENTS matches no files in {templates_dir}"
        )
    return matches


def _context_document_key(path: Path, templates_dir: Path) -> str:
    """Имя, под которым документ из `extra_paths` прикладывается к вызову.

    Файл внутри `templates_dir` получает тот же вид ключа, что и документы
    фиксированного списка, — путь относительно `templates_dir`. Так он не
    прикладывается второй раз, если уже есть в фиксированном списке
    (например, `base_template_refs` со ссылкой на генератор из
    `generators/*.cpp`), а ключ (он входит в `compute_prompt_hash`) не
    зависит от того, как записан сам `templates_dir` — относительно или
    абсолютно. Файл вне `templates_dir` остаётся под путём как он передан.
    """
    try:
        return path.resolve().relative_to(templates_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_context_documents(
    step_name: str,
    *,
    templates_dir: Path = TEMPLATES_DIR,
    extra_paths: list[str] | None = None,
) -> dict[str, str]:
    """Читает документы контекста для шага `step_name`.

    Порядок: сначала фиксированный список `STEP_CONTEXT_DOCUMENTS[step_name]`
    (пути относительно `templates_dir`; glob-записи раскрываются в
    отсортированный список файлов), затем `extra_paths` — пути
    относительно корня репозитория, специфичные для конкретного вызова шага
    (например, `generation.base_template_refs`, см. CLAUDE.md,
    "base_template_refs"), а не для всех вызовов шага вообще.

    Отсутствующий файл (или glob-запись без единого совпадения) — понятная
    ошибка ДО вызова модели, с указанием пути и того, откуда он взят
    (фиксированный список или `extra_paths`) — не падаем молча и не
    пропускаем документ.
    """
    documents: dict[str, str] = {}

    for entry in STEP_CONTEXT_DOCUMENTS.get(step_name, []):
        for rel_path in _expand_context_entry(step_name, entry, Path(templates_dir)):
            documents[rel_path] = (Path(templates_dir) / rel_path).read_text(encoding="utf-8")

    for rel_path in extra_paths or []:
        path = Path(rel_path)
        key = _context_document_key(path, Path(templates_dir))
        if key in documents:
            continue
        if not path.exists():
            raise FileNotFoundError(
                f"Step '{step_name}': extra context document "
                f"'{rel_path}' (extra_context_documents) not found at {path}"
            )
        documents[key] = path.read_text(encoding="utf-8")

    return documents


def _write_artifacts(
    artifacts: dict[str, str],
    resolve_path: Callable[[str], Path],
) -> dict[str, Path]:
    written: dict[str, Path] = {}
    for filename, content in artifacts.items():
        path = resolve_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written


def run_generative_step(
    *,
    step_name: str,
    problem_id: str,
    context: dict[str, Any],
    input_hash: str,
    resolve_artifact_path: Callable[[str], Path],
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
    validate_artifacts: Callable[[dict[str, str]], None] | None = None,
) -> StepResult:
    """Общий цикл шага: рендер промптов, вызов модели, запись результата.

    `context` — уже готовый для Jinja словарь (секция спека шага +
    апстрим-артефакты, если нужны шагу); нормализацию `None -> []` для
    перечисляемых полей (см. `with_default_lists`) должен сделать
    вызывающий модуль конкретного шага, а не эта функция — у каждого шага
    свой набор таких полей.

    `input_hash` — уже посчитанный `cache.compute_input_hash(...)` для этого
    шага; сюда же передаётся вызывающим модулем, поскольку только он знает,
    какая секция спека и какие апстрим-артефакты на шаг влияют (см.
    CLAUDE.md, "Кэширование по хешу спека").

    `resolve_artifact_path(filename)` — как разложить `artifacts` ответа
    модели по каталогам `outputs/<problem_id>/...` (у каждого шага своя
    раскладка, см. CLAUDE.md, "Структура каталогов").

    `extra_context_documents` — дополнительные пути (относительно корня
    репозитория), специфичные для конкретного вызова этого шага, а не для
    всех его вызовов вообще (`generators_and_script` —
    `generation.base_template_refs`, `solutions_draft` — шаблоны решений на
    языках из `solutions.languages`, см. `load_context_documents`). Вместе с фиксированным списком
    `STEP_CONTEXT_DOCUMENTS[step_name]` они читаются и передаются модели как
    `context_documents`, а также входят в `compute_prompt_hash` — правка
    любого из этих документов должна инвалидировать кэш шага.

    `validate_artifacts(artifacts)` — необязательная проверка набора
    артефактов из ответа модели (например, что имена файлов ровно те, что
    ожидает шаг). Вызывается только при `confirmed`/`proposed` (при
    `uncertain` `artifacts` по контракту может быть пустым/частичным) и
    строго ДО записи на диск: если она бросает `ValueError`, исключение
    всплывает из этой функции как есть, в `outputs/` не пишется ни один
    файл и `CacheEntry` не сохраняется.

    При `status: uncertain` бросает `StepUncertainError` и не пишет ничего в
    `outputs/`. При `confirmed`/`proposed` пишет артефакты, компилирует все
    записанные `.cpp`-файлы через `compile_check` (без запуска — см.
    CLAUDE.md, "Минимальная страховка вместо судящего контура") и сохраняет
    `CacheEntry` с этим `input_hash`, актуальным хешем промптов и моделью
    шага из `model_router.STEP_TO_MODEL`.
    """
    system_prompt = read_system_prompt(step_name, prompts_dir=prompts_dir)
    user_prompt = render_user_prompt(step_name, context, prompts_dir=prompts_dir)
    context_documents = load_context_documents(
        step_name, templates_dir=templates_dir, extra_paths=extra_context_documents
    )

    response = model_router.call_model(
        step_name, system_prompt, user_prompt, context_documents=context_documents
    )

    if response.status not in _VALID_STATUSES:
        raise ValueError(
            f"Step '{step_name}': the model returned an unknown status "
            f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
        )

    if response.status == STATUS_UNCERTAIN:
        raise StepUncertainError(step_name, problem_id, response.notes)

    if validate_artifacts is not None:
        validate_artifacts(response.artifacts)

    artifact_paths = _write_artifacts(response.artifacts, resolve_artifact_path)

    compile_results: dict[str, CompileResult] = {
        filename: compile_check(path, Path(templates_dir) / COMMON_SUBDIR)
        for filename, path in artifact_paths.items()
        if path.suffix == ".cpp"
    }

    prompt_hash = compute_prompt_hash(
        step_name,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )
    save_cache_entry(
        problem_id,
        step_name,
        CacheEntry(
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            model=model_router.STEP_TO_MODEL[step_name],
            status=response.status,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        outputs_dir=outputs_dir,
    )

    return StepResult(
        step_name=step_name,
        problem_id=problem_id,
        status=response.status,
        artifact_paths=artifact_paths,
        notes=response.notes,
        compile_results=compile_results,
    )
