"""Общий цикл одного генеративного шага.

Каждый из шагов (`statement_draft`, `constraints_pick`,
`generators_and_script`, `solutions_draft`, `checker_draft`,
`interactor_draft`, см. CLAUDE.md,
"Роль каждого генеративного шага") — это одна и та же последовательность
действий над разными данными:

1. отрендерить `prompts/<step>/user.md.j2` (Jinja2) из секции спека
   (+ апстрим-артефактов, если шаг от них зависит);
2. прочитать `prompts/<step>/system.md` как системный промпт;
3. вызвать `model_router.call_model(...)`;
4. разобрать `ModelResponse.status` по "Правилу эскалации при неуверенности"
   (CLAUDE.md): `uncertain` — остановить шаг и не писать артефакты;
   `confirmed`/`proposed` — записать артефакты в `problems/<problem_id>/...`,
   прогнать `compile_check` по всем `.cpp`-артефактам и сохранить
   `CacheEntry` через `cache.py`.

Режим доработки (`StepRevision`): если файлы шага уже есть на диске, модель
получает их вместе с тем, что изменилось во входе шага с прошлого прогона
(`prompts/revise.md.j2`), и дорабатывает их, а не пишет с нуля. Каждый
текущий файл в ответе — ровно в одном из: `artifacts` (изменён),
`unchanged`, `removed`, `renamed[].from` (`_plan_response`). Удалённые
файлы не удаляются, а переименовываются в `<имя>.removed`
(`REMOVED_SUFFIX`) — автор посмотрит и удалит сам; переименования
применяются сразу. Прежние версии перезаписанных и переименованных файлов
(в любом режиме) — в `.llm_backup/<время>/` каталога задачи
(`problems/` вне git, истории у файлов задачи нет).

Ответ применяется как `git merge`: то, что применить нельзя (недопустимое
имя файла, текущий файл сразу в нескольких местах ответа, переименование в
занятое имя), — конфликт: файл задачи остаётся как был, версия модели
откладывается в `.llm_conflicts/<шаг>-<время>/` с отчётом `CONFLICTS.md`, а
остальное применяется. Шаги, файлы которых связаны друг с другом
(`partial_apply=False`), и ответ, не прошедший `validate_artifacts`,
применяются только целиком: при конфликте не применяется ничего. Запись
кэша с конфликтами — не cache hit, пока автор их не разберёт и не сделает
`llm accept`.

Неполный ответ — без части файлов, которые шаг обязан вернуть
(`MissingArtifactsError` из `validate_artifacts`), — модель один раз
дописывает отдельным вызовом (`MISSING_ARTIFACTS_PROMPT`): ей показываются
уже возвращённые файлы, и она возвращает только недостающие.

Этот модуль реализует шаги 1-5 один раз (`run_generative_step`); конкретные
модули в `orchestrator/steps/*.py` только знают, какую секцию спека читать,
как разложить артефакты по каталогам `problems/<id>/...` и что входит в
апстрим-хеш — сами не дублируют этот цикл.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

import jinja2
import yaml

from orchestrator import model_router
from orchestrator.cache import (
    REVISE_PROMPT,
    CacheEntry,
    PendingResponse,
    changed_input_values,
    compute_prompt_hash,
    compute_prompt_parts,
    discard_pending_response,
    input_parts_from_values,
    save_cache_entry,
    save_pending_response,
)
from orchestrator.checks.compile_check import CompileResult, compile_check
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    EXAMPLES_DIR,
    PROBLEMS_DIR,
    REMOVED_SUFFIX,
    is_authoring_name,
    is_removed_mark,
)

PROMPTS_DIR = Path("prompts")
TEMPLATES_DIR = Path("templates")

# `REVISE_PROMPT` (импортирован из `cache.py`) — общий для всех шагов блок
# user-промпта режима доработки (относительно prompts_dir): дописывается к
# `prompts/<step>/user.md.j2`, когда модели показаны текущие файлы шага.

# Общий для всех шагов блок user-промпта дозапроса недостающих файлов
# (относительно prompts_dir): дописывается к user-промпту первого вызова,
# если его ответ не содержал части обязательных файлов. В хеш промпта шага
# не входит: результат шага он не задаёт — только просит дописать то, что
# основной промпт и так требовал.
MISSING_ARTIFACTS_PROMPT = "missing_artifacts.md.j2"

# Каталог (в каталоге задачи) с прежними версиями файлов, которые шаг
# перезаписал или переименовал: `.llm_backup/<время>/<путь в каталоге задачи>`.
LLM_BACKUP_DIRNAME = ".llm_backup"
# Каталог (в каталоге задачи) с тем, что из ответа модели не применено:
# `.llm_conflicts/<шаг>-<время>/` — версии файлов от модели и отчёт
# `CONFLICTS_REPORT`; файлы с недопустимыми именами — в `INVALID_NAMES_SUBDIR`.
LLM_CONFLICTS_DIRNAME = ".llm_conflicts"
CONFLICTS_REPORT = "CONFLICTS.md"
# Причина конфликта у частей ответа, не применённого целиком: сама причина
# одна на весь ответ (`StepResult.not_applied_reason`), у файлов не повторяется.
WHOLE_RESPONSE_NOT_APPLIED = "the response is not applied as a whole"
INVALID_NAMES_SUBDIR = "invalid_names"
# Учебные задачи-образцы `examples/` (`EXAMPLES_DIR`), каждая в раскладке
# `problems/<problem_id>/` плюс свой спек `spec.yaml` (как задачи раскладываются
# по вложенным каталогам — см. `orchestrator/problem_paths.py`). Сами по себе к
# вызову не прикладываются — только как референсы спека (`orchestrator/references.py`).

# У каждой задачи своя копия `problem_lib.h` — `problems/<problem_id>/problem_lib.h`:
# шаблон `templates/problem_lib.h` — только её начальное содержимое, и правки
# автора под конкретную задачу не перезаписывают ни шаблон, ни
# `llm generate`, ни `polygon push`. Копию создаёт `ensure_problem_lib` —
# перед compile-check сгенерированных `.cpp` и в polygon-шаге
# `upload_problem_lib`; `polygon pull` скачивает в неё resource-файл с
# Polygon. К вызову модели прикладывается шаблон (`STEP_CONTEXT_DOCUMENTS`).
PROBLEM_LIB_FILENAME = "problem_lib.h"

# Правила для интерактивных задач (`spec.interactive`): `interactor_draft`
# получает их всегда, `statement_draft` и `solutions_draft` — только для
# интерактивной задачи (через `extra_context_documents`), чтобы их правка не
# инвалидировала кэш этих шагов у обычных задач.
INTERACTIVE_TUTORIAL = "tutorials/interactive.md"

# Обязательные документы контекста на шаг (пути относительно templates_dir),
# см. CLAUDE.md, "Как использовать приложенные материалы": tutorials/*.md —
# это правила, а не справочный материал. Правила разбиты по темам, и шаг
# получает только нужные ему темы: правка одной темы инвалидирует кэш только
# читающих её шагов (tutorials/polygon.md — справка по интерфейсу Polygon,
# ни одному шагу не прикладывается). `constraints_format.yaml` — формат
# результата `constraints_pick`, который читают следующие шаги и
# оркестратор (обязателен и при спеке без референсов), а problem_lib.h —
# основа, которую нужно расширять. Промпты (prompts/<step>/system.md) лишь ссылаются на эти
# файлы по имени — здесь их содержимое реально прикладывается к вызову
# модели (см. `call_model` в `model_router.py` и `LLMClient.generate`), а не
# пересказывается.
#
# Запись с `*` — glob-шаблон относительно templates_dir: прикладываются все
# подходящие файлы (в отсортированном порядке). Образцы (файлы других задач)
# здесь не перечислены — они зависят от референсов конкретного спека, см.
# `extra_context_documents` шагов и `orchestrator/references.py`.
STEP_CONTEXT_DOCUMENTS: dict[str, list[str]] = {
    "statement_draft": [
        "tutorials/statement.md",
    ],
    "constraints_pick": [
        "tutorials/limits.md",
        "tutorials/validator.md",
        "constraints_format.yaml",
        "problem_lib.h",
    ],
    "generators_and_script": [
        "tutorials/tests.md",
        "tutorials/freemarker.md",
        "problem_lib.h",
        "test_script_functions.ftl",
    ],
    "solutions_draft": [
        "tutorials/solutions.md",
    ],
    "checker_draft": [
        "tutorials/checker.md",
        "problem_lib.h",
    ],
    "interactor_draft": [
        INTERACTIVE_TUTORIAL,
        "problem_lib.h",
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
    `problems/` — этот класс исключения и есть тот механический стоп-сигнал.
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
            f"{details}. Fix problems/{problem_id}/spec.yaml and run the step again."
        )


class MissingArtifactsError(ValueError):
    """Ответ модели неполон: в нём нет части файлов, которые шаг обязан
    вернуть (`missing` — их имена, как их вернула бы модель). Бросается из
    `validate_artifacts` шага (`check_artifact_names`); `run_generative_step`
    один раз дозапрашивает эти файлы у модели."""

    def __init__(self, message: str, missing: list[str]):
        super().__init__(message)
        self.missing = missing


def check_artifact_names(step_name: str, expected: set[str], artifacts: dict[str, str]) -> None:
    """Проверка набора имён файлов шага: ровно `expected`. Недостача —
    `MissingArtifactsError` (её можно дозапросить), только лишние файлы —
    `ValueError`."""
    actual = set(artifacts)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        message = f"Step '{step_name}': incomplete response — missing files: {missing}"
        if extra:
            message += f"; unexpected files: {extra}"
        raise MissingArtifactsError(message, missing)
    if extra:
        raise ValueError(f"Step '{step_name}': unexpected files in the response: {extra}")


@dataclass
class GeneratedArtifacts:
    """Файлы шага, которые пишет оркестратор, а не модель (например, input
    примеров условия — копия из спека): модель их не возвращает, а если
    вернула — её версия отбрасывается (заметка `ignored`).

    `artifacts` — имя (как его вернула бы модель) -> содержимое; входят в
    набор, который проверяет `validate_artifacts`, и пишутся вместе с
    ответом модели — только если он применён. `stale` — такие же файлы
    прошлых прогонов, которых больше нет (имя -> путь): помечаются
    `.removed`, как удалённые моделью."""

    artifacts: dict[str, str]
    stale: dict[str, Path] = field(default_factory=dict)
    reason: str = "written by the orchestrator from the spec"


@dataclass
class StepResult:
    """Результат успешного (`confirmed`/`proposed`) прогона шага.

    `notes` сохраняет заметки модели как есть (в т.ч. `kind: proposed`) —
    какие поля отмечать как "требует ручной проверки автором" в
    `problems/` решает вызывающий код (пайплайн), см. CLAUDE.md, "Правило
    эскалации при неуверенности".
    """

    step_name: str
    problem_id: str
    status: str
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    compile_results: dict[str, CompileResult] = field(default_factory=dict)
    # Режим доработки: файлы, помеченные `.removed` (путь пометки, причина),
    # и переименования (старый путь, новый путь, причина).
    revised: bool = False
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    # Куда легли прежние версии перезаписанных/переименованных файлов;
    # `None` — ничего не перезаписано.
    backup_dir: Optional[Path] = None
    # Что из ответа модели не применено (имя файла, причина) и каталог, куда
    # отложены версии модели (`_write_conflicts`); `None` — всё применено.
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    conflicts_dir: Optional[Path] = None
    # Почему ответ не применён целиком (не прошёл `validate_artifacts` или
    # конфликт в шаге, применяемом только целиком); `None` — применён.
    not_applied_reason: Optional[str] = None


@dataclass
class StepRevision:
    """Режим доработки: шаг уже запускался, и его файлы есть на диске.

    `current_paths` — текущие файлы шага: имя артефакта (как его вернула бы
    модель, см. `current_artifacts` шагов) -> путь. `previous_input_values`
    — вход прошлого прогона по частям (`CacheEntry.input_values`), по нему
    модели показывается, что изменилось; `None` — неизвестно (записи кэша
    нет или она старше `input_values`). `other_changes` — что ещё
    изменилось с прошлого прогона, кроме входа (модель, промпт, приложенные
    документы), — только имена, без содержимого.
    """

    current_paths: dict[str, Path]
    previous_input_values: Optional[dict[str, Any]] = None
    other_changes: list[str] = field(default_factory=list)


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

    Берётся именно спек, а не сгенерированные `problems/<id>/statement/*.tex`:
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
    печатать ответ, — и у интерактивной задачи протокол взаимодействия
    (`interaction_sketch`; у обычной ключа нет, чтобы не менять хеш).
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
        if statement.has_interaction:
            section["interaction_sketch"] = statement.interaction_sketch
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


def _display_value(value: Any) -> Optional[str]:
    """Часть входа шага текстом для промпта: строка — как есть (текст
    легенды, содержимое `constraints.yaml`), списки и словари — YAML,
    прочие значения — JSON (`true`, `5`, `null` у поля, заданного как null)."""
    if isinstance(value, str):
        return value
    if not isinstance(value, (dict, list)):
        return json.dumps(value)
    return yaml.safe_dump(
        value, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")


def render_revision_prompt(
    revision: StepRevision,
    input_values: dict[str, Any],
    *,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    """Блок режима доработки (`prompts/revise.md.j2`): текущие файлы шага и
    что изменилось во входе шага с прошлого прогона (`None` у "было" —
    части не было, у "стало" — её больше нет; поле со значением null —
    `null`)."""
    previous = revision.previous_input_values
    changes = changed_input_values(previous, input_values)
    context = {
        "current_files": [
            {"name": name, "content": path.read_text(encoding="utf-8").rstrip("\n")}
            for name, path in revision.current_paths.items()
        ],
        "changes": None
        if changes is None
        else [
            {
                "name": name,
                "old": _display_value(old) if name in previous else None,
                "new": _display_value(new) if name in input_values else None,
            }
            for name, old, new in changes
        ],
        "other_changes": revision.other_changes,
    }
    return _jinja_env(prompts_dir).get_template(REVISE_PROMPT).render(**context)


def render_missing_artifacts_prompt(
    missing: list[str], returned: dict[str, str], *, prompts_dir: Path = PROMPTS_DIR
) -> str:
    """Блок дозапроса (`MISSING_ARTIFACTS_PROMPT`): каких файлов не было в
    ответе и что модель уже вернула."""
    context = {
        "missing": missing,
        "returned_files": [
            {"name": name, "content": content.rstrip("\n")} for name, content in returned.items()
        ],
    }
    return _jinja_env(prompts_dir).get_template(MISSING_ARTIFACTS_PROMPT).render(**context)


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
    фиксированного списка, — путь относительно `templates_dir`
    (`problem_lib.h`). Так он не прикладывается второй
    раз, если на него ведут два референса (общий каталог и шаговый файл из
    него), а ключ (он входит в `compute_prompt_hash`) не зависит от того,
    как записан сам `templates_dir` — относительно или абсолютно. Файл вне
    `templates_dir` остаётся под путём как он передан
    (`examples/<problem_id>/validator.cpp`, `problems/<problem_id>/validator.cpp`),
    кроме файлов `authoring/` (спек, `constraints.yaml`): они — под путём без
    `authoring/` (`examples/<problem_id>/constraints.yaml`), под тем же
    именем, под которым шаг возвращает свой `constraints.yaml`, — и ключ
    (а с ним хеш промпта) тот же, что до переноса этих файлов в `authoring/`.
    """
    try:
        return path.resolve().relative_to(templates_dir.resolve()).as_posix()
    except ValueError:
        if path.parent.name == AUTHORING_DIRNAME and is_authoring_name(path.name):
            return str(path.parent.parent / path.name)
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
    (файлы референсов спека, см. `orchestrator/references.py`), а не для
    всех вызовов шага вообще.

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


def template_problem_lib_path(templates_dir: Path = TEMPLATES_DIR) -> Path:
    return Path(templates_dir) / PROBLEM_LIB_FILENAME


def problem_lib_path(problem_id: str, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return Path(problems_dir) / problem_id / PROBLEM_LIB_FILENAME


def ensure_problem_lib(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR, templates_dir: Path = TEMPLATES_DIR
) -> Path:
    """Путь к `problem_lib.h` задачи; если его нет — сначала копирует туда
    шаблон. Нет и шаблона — `FileNotFoundError`."""
    path = problem_lib_path(problem_id, problems_dir)
    if not path.exists():
        template = template_problem_lib_path(templates_dir)
        if not template.exists():
            raise FileNotFoundError(f"template {template} not found — check templates_dir")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
    return path


def artifact_filename(filename: str, subdir: str | None = None) -> str:
    """Имя файла артефакта без каталога, для шагов, которые сами решают, в
    какой каталог `problems/<problem_id>/` его положить. Заголовки образцов,
    которые видит модель, — пути вида `.../generators/gen_rand.cpp`, и
    модель может вернуть ключ `generators/gen_rand.cpp` вместо
    `gen_rand.cpp`: префикс `subdir` (каталог, куда шаг и так кладёт файл)
    снимается. Любой другой путь — `ValueError`: иначе файл лёг бы во
    вложенный каталог (`generators/generators/`) или за пределы задачи."""
    parts = PurePosixPath(filename).parts
    if subdir is not None and len(parts) == 2 and parts[0] == subdir:
        parts = parts[1:]
    if len(parts) != 1 or parts[0] in (".", ".."):
        expected = f"'<name>' or '{subdir}/<name>'" if subdir else "'<name>'"
        raise ValueError(
            f"artifact name '{filename}' from the model is not a plain file name "
            f"(expected {expected})"
        )
    return parts[0]


def existing_artifacts(candidates: dict[str, Path]) -> dict[str, Path]:
    """Те из `candidates` (имя артефакта -> путь), что есть на диске и не
    помечены как удалённые, — для `current_artifacts` шагов."""
    return {
        name: path
        for name, path in candidates.items()
        if path.is_file() and not is_removed_mark(path)
    }


def directory_artifacts(directory: Path, pattern: str = "*") -> dict[str, Path]:
    """Файлы `directory` по `pattern` под своими именами (по алфавиту), кроме
    помеченных как удалённые."""
    if not directory.is_dir():
        return {}
    return existing_artifacts(
        {path.name: path for path in sorted(directory.glob(pattern), key=lambda p: p.name)}
    )


@dataclass
class _Conflict:
    """Часть ответа модели, которая не применена: версия файла от модели
    (`content`, если есть) откладывается в каталог конфликтов
    (`LLM_CONFLICTS_DIRNAME`), файл задачи остаётся как был."""

    filename: str
    reason: str
    content: Optional[str] = None


@dataclass
class _ApplyPlan:
    """Что сделать с файлами шага по ответу модели.

    `artifacts` — новые и изменённые файлы к записи; `final_artifacts` —
    итоговый набор файлов шага (его проверяет `validate_artifacts`):
    записанные + оставшиеся как есть + переименованные, без удалённых;
    `conflicts` — что из ответа не применено; `notes` — что применено не
    буквально (например, текущий файл, о котором ответ молчит, оставлен как
    есть)."""

    artifacts: dict[str, str]
    final_artifacts: dict[str, str]
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    conflicts: list[_Conflict] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)


def _note(field_name: str, kind: str, explanation: str) -> dict[str, Any]:
    return {"field": field_name, "kind": kind, "explanation": explanation}


def _plan_response(
    response: Any,
    revision: Optional[StepRevision],
    resolve_path: Callable[[str], Path],
) -> _ApplyPlan:
    """Раскладывает ответ модели на то, что можно применить, и конфликты —
    как `git merge`: применимое применяется, остальное откладывается автору.

    Файл с недопустимым именем (`resolve_path` бросает `ValueError`) —
    конфликт. В режиме доработки каждый текущий файл шага должен быть ровно
    в одном из `artifacts`/`unchanged`/`removed`/`renamed[].from` (файлы
    сопоставляются по пути, а не по строке имени: `generators/gen.cpp` и
    `gen.cpp` — один файл):
    - ни в одном — оставляется как есть (заметка `kept`);
    - в нескольких — конфликт: текущий файл не трогается, версия модели
      откладывается;
    - неизвестное имя в `unchanged`/`removed`/`renamed[].from` —
      игнорируется (заметка `ignored`);
    - переименование в занятое или повторяющееся имя — конфликт: файл не
      переименовывается, его новая версия (если есть) откладывается."""
    plan = _ApplyPlan(artifacts={}, final_artifacts={})
    for filename, content in response.artifacts.items():
        try:
            resolve_path(filename)
        except ValueError as exc:
            plan.conflicts.append(_Conflict(filename, str(exc), content))
        else:
            plan.artifacts[filename] = content

    if revision is None:
        if response.unchanged or response.removed or response.renamed:
            plan.notes.append(_note(
                "response",
                "ignored",
                "модель вернула unchanged/removed/renamed, хотя текущие файлы ей "
                "не показывались (генерация с нуля), — проигнорировано",
            ))
        plan.final_artifacts = dict(plan.artifacts)
        return plan

    current_paths = revision.current_paths
    current_by_path = {path.resolve(): name for name, path in current_paths.items()}

    def current_name(filename: str) -> Optional[str]:
        try:
            return current_by_path.get(resolve_path(filename).resolve())
        except ValueError:
            return None

    def ignored(where: str, filename: str) -> None:
        plan.notes.append(_note(
            filename, "ignored", f"{where}: not a current file of the step — ignored"
        ))

    places: dict[str, list[str]] = {name: [] for name in current_paths}
    modified: dict[str, str] = {}  # текущее имя -> имя в artifacts ответа
    for filename in plan.artifacts:
        name = current_name(filename)
        if name is not None:
            places[name].append("artifacts")
            modified[name] = filename
    unchanged: list[str] = []
    for filename in response.unchanged:
        name = current_name(filename)
        if name is None:
            ignored("unchanged", filename)
        else:
            places[name].append("unchanged")
            unchanged.append(name)
    removed: list[tuple[str, str]] = []
    for entry in response.removed:
        name = current_name(entry["filename"])
        if name is None:
            ignored("removed", entry["filename"])
        else:
            places[name].append("removed")
            removed.append((name, entry["reason"]))
    renamed: list[tuple[str, dict[str, str]]] = []
    for entry in response.renamed:
        name = current_name(entry["from"])
        if name is None:
            ignored("renamed.from", entry["from"])
        else:
            places[name].append("renamed.from")
            renamed.append((name, entry))

    contested = {name for name, where in places.items() if len(where) > 1}
    for name in sorted(contested):
        filename = modified.pop(name, name)
        plan.conflicts.append(_Conflict(
            filename,
            f"in several places of the response ({', '.join(places[name])}) "
            "— the current file is kept as is",
            plan.artifacts.pop(filename, None),
        ))
    for name, where in places.items():
        if not where:
            plan.notes.append(_note(
                name, "kept", "not mentioned in the response — kept as is"
            ))

    gone: set[str] = set(modified)
    for name, reason in removed:
        if name not in contested:
            plan.removed.append((current_paths[name], reason))
            gone.add(name)
    targets: set[Path] = set()
    renamed_contents: dict[str, str] = {}
    for name, entry in renamed:
        to = entry["to"]
        if name in contested:
            problem = f"new name of '{name}', which is in several places of the response"
        else:
            try:
                target = resolve_path(to)
            except ValueError as exc:
                problem = str(exc)
            else:
                if target.resolve() in current_by_path or target.exists():
                    problem = f"'{name}' -> '{to}': the new name is already taken"
                elif target.resolve() in targets:
                    problem = f"'{name}' -> '{to}': the new name is used twice"
                else:
                    problem = None
        if problem is not None:
            if name not in contested:
                problem += f" — '{name}' is not renamed"
            plan.conflicts.append(_Conflict(to, problem, plan.artifacts.pop(to, None)))
            continue
        targets.add(target.resolve())
        plan.renamed.append((current_paths[name], target, entry["reason"]))
        renamed_contents[to] = current_paths[name].read_text(encoding="utf-8")
        gone.add(name)

    plan.final_artifacts = {
        name: path.read_text(encoding="utf-8")
        for name, path in current_paths.items()
        if name not in gone
    }
    plan.final_artifacts.update(renamed_contents)
    plan.final_artifacts.update(plan.artifacts)
    return plan


def _add_generated_artifacts(
    plan: _ApplyPlan, generated: GeneratedArtifacts, resolve_path: Callable[[str], Path]
) -> set[str]:
    """Файлы оркестратора — в итоговый набор файлов шага; их версии от
    модели отбрасываются (возвращаются их имена в ответе)."""
    dropped: set[str] = set()
    generated_paths = {resolve_path(name).resolve() for name in generated.artifacts}
    for filename in list(plan.artifacts):
        if resolve_path(filename).resolve() in generated_paths:
            del plan.artifacts[filename]
            plan.final_artifacts.pop(filename, None)
            dropped.add(filename)
            plan.notes.append(_note(
                filename, "ignored", f"returned by the model — ignored: {generated.reason}"
            ))
    plan.final_artifacts.update(generated.artifacts)
    return dropped


def _plan_and_check(
    response: Any,
    revision: Optional[StepRevision],
    resolve_path: Callable[[str], Path],
    generated: Optional[GeneratedArtifacts],
    validate_artifacts: Optional[Callable[[dict[str, str]], None]],
    partial_apply: bool,
) -> tuple[_ApplyPlan, Optional[str], list[str]]:
    """`_plan_response` + проверка итогового набора файлов шага. Возвращает
    план, причину, по которой ответ не применяется целиком (`None` —
    применяется), и недостающие файлы (`MissingArtifactsError`)."""
    plan = _plan_response(response, revision, resolve_path)
    dropped: set[str] = set()
    if generated is not None:
        dropped = _add_generated_artifacts(plan, generated, resolve_path)
    reason: Optional[str] = None
    missing: list[str] = []
    if plan.conflicts and not partial_apply:
        reason = "the files of this step are applied only all together"
    elif validate_artifacts is not None:
        try:
            validate_artifacts(plan.final_artifacts)
        except MissingArtifactsError as exc:
            reason, missing = str(exc), exc.missing
        except ValueError as exc:
            reason = str(exc)
    if reason is not None:
        plan = _whole_step_conflict(response, plan, ignored=dropped)
    return plan, reason, missing


def _merge_missing_artifacts(
    response: ModelResponse,
    retry: ModelResponse,
    missing: list[str],
    resolve_path: Callable[[str], Path],
) -> ModelResponse:
    """Ответ с файлами из дозапроса: из него берутся только недостающие
    файлы (остальные — заметкой `ignored`), заметки обоих ответов
    складываются, статус — худший из двух."""

    def resolved(filename: str) -> Optional[Path]:
        try:
            return resolve_path(filename).resolve()
        except ValueError:
            return None

    wanted = {resolved(name) for name in missing} - {None}
    added = {
        filename: content
        for filename, content in retry.artifacts.items()
        if resolved(filename) in wanted
    }
    notes = [
        *response.notes,
        *retry.notes,
        _note(
            "response",
            "retried",
            f"the response lacked {missing} — asked for them in a separate call; "
            f"returned: {sorted(added) or 'none of them'}",
        ),
    ]
    ignored = sorted(set(retry.artifacts) - set(added))
    if ignored:
        notes.append(_note(
            "response", "ignored", f"files from the follow-up call that were not asked for: {ignored}"
        ))
    statuses = {response.status, retry.status}
    if retry.status not in _VALID_STATUSES or STATUS_UNCERTAIN in statuses:
        status = retry.status
    elif STATUS_PROPOSED in statuses:
        status = STATUS_PROPOSED
    else:
        status = STATUS_CONFIRMED
    return replace(
        response, status=status, artifacts={**response.artifacts, **added}, notes=notes
    )


def _whole_step_conflict(
    response: Any, plan: _ApplyPlan, *, ignored: set[str] = frozenset()
) -> _ApplyPlan:
    """Ответ не применяется целиком: все файлы модели и её предложения
    удалить/переименовать — в конфликты, файлы шага не трогаются. Причина —
    одна на весь ответ (`StepResult.not_applied_reason`, начало
    `CONFLICTS.md`), у файлов она не повторяется. `ignored` — файлы ответа,
    отброшенные и при применении (`GeneratedArtifacts`)."""
    conflicts = list(plan.conflicts)
    set_aside = {conflict.filename for conflict in conflicts if conflict.content is not None}
    for filename, content in response.artifacts.items():
        if filename not in set_aside | ignored:
            conflicts.append(_Conflict(filename, WHOLE_RESPONSE_NOT_APPLIED, content))
    for entry in response.removed:
        conflicts.append(_Conflict(
            entry["filename"], f"proposed removal ({entry['reason']}): {WHOLE_RESPONSE_NOT_APPLIED}"
        ))
    for entry in response.renamed:
        conflicts.append(_Conflict(
            entry["from"],
            f"proposed rename to '{entry['to']}' ({entry['reason']}): {WHOLE_RESPONSE_NOT_APPLIED}",
        ))
    return _ApplyPlan(
        artifacts={}, final_artifacts={}, conflicts=conflicts, notes=plan.notes
    )


class _Backup:
    """Прежние версии файлов задачи — в `.llm_backup/<время>/` (создаётся при
    первом файле)."""

    def __init__(self, problem_dir: Path) -> None:
        self.problem_dir = problem_dir
        self.directory: Optional[Path] = None

    def save(self, path: Path) -> None:
        if self.directory is None:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.directory = self.problem_dir / LLM_BACKUP_DIRNAME / stamp
        target = self.directory / path.relative_to(self.problem_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _write_artifacts(
    artifacts: dict[str, str],
    resolve_path: Callable[[str], Path],
    backup: _Backup,
    *,
    skip_backup: set[Path] = frozenset(),
) -> dict[str, Path]:
    written: dict[str, Path] = {}
    for filename, content in artifacts.items():
        path = resolve_path(filename)
        if (
            path.is_file()
            and path not in skip_backup
            and path.read_text(encoding="utf-8") != content
        ):
            backup.save(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "file"


def _write_conflicts(
    step_name: str,
    conflicts: list[_Conflict],
    problem_dir: Path,
    resolve_path: Callable[[str], Path],
    whole_step_reason: Optional[str],
) -> Path:
    """Каталог `.llm_conflicts/<шаг>-<время>/`: версии файлов от модели (по
    их путям в каталоге задачи; файлы с недопустимыми именами — в
    `INVALID_NAMES_SUBDIR`) и `CONFLICTS_REPORT` — что и почему не
    применено."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = problem_dir / LLM_CONFLICTS_DIRNAME / f"{step_name}-{stamp}"
    lines = [f"# Not applied: {step_name}", ""]
    if whole_step_reason is not None:
        lines += [f"Nothing of the response is applied: {whole_step_reason}.", ""]
    for conflict in conflicts:
        line = f"- `{conflict.filename}` — {conflict.reason}"
        if conflict.content is not None:
            try:
                relative = resolve_path(conflict.filename).relative_to(problem_dir)
            except ValueError:
                relative = Path(INVALID_NAMES_SUBDIR) / _safe_filename(conflict.filename)
            target = directory / relative
            while target.exists():
                target = target.with_name(target.name + "_")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(conflict.content, encoding="utf-8")
            line += f"; model version: `{target.relative_to(directory).as_posix()}`"
        lines.append(line)
    lines += [
        "",
        "The files of the step are left as they were for everything above. Resolve "
        f"by hand, delete this directory, then run `llm accept --step {step_name}`.",
    ]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CONFLICTS_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def _apply_removals_and_renames(plan: _ApplyPlan, backup: _Backup) -> list[tuple[Path, str]]:
    """Помечает удалённые файлы (`<имя>.removed`; прежняя пометка с тем же
    именем — в бэкап) и переименовывает файлы (старая версия — в бэкап).
    Возвращает `(путь пометки, причина)` удалённых."""
    marks: list[tuple[Path, str]] = []
    for path, reason in plan.removed:
        mark = path.with_name(path.name + REMOVED_SUFFIX)
        if mark.exists():
            backup.save(mark)
        path.replace(mark)
        marks.append((mark, reason))
    for old_path, new_path, _ in plan.renamed:
        backup.save(old_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.replace(new_path)
    return marks


def run_generative_step(
    *,
    step_name: str,
    problem_id: str,
    context: dict[str, Any],
    input_hash: str,
    resolve_artifact_path: Callable[[str], Path],
    input_values: dict[str, Any] | None = None,
    revision: StepRevision | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    extra_context_documents: list[str] | None = None,
    validate_artifacts: Callable[[dict[str, str]], None] | None = None,
    postprocess_artifacts: (
        Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]] | None
    ) = None,
    saved_response: ModelResponse | None = None,
    partial_apply: bool = False,
    generated_artifacts: GeneratedArtifacts | None = None,
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
    CLAUDE.md, "Кэширование по хешу спека"). `input_values` — тот же вход по
    частям (`cache.compute_input_values`): он и его хеши по частям
    сохраняются в `CacheEntry`, чтобы `llm status` мог сказать, что именно
    изменилось, а следующий прогон в режиме доработки — показать это модели.

    `revision` — режим доработки (см. докстринг модуля): к user-промпту
    дописывается `prompts/revise.md.j2` с текущими файлами шага и
    изменениями входа; ответ раскладывается `_plan_response`, удалённые
    файлы помечаются `.removed`, переименования применяются. `None` — модель
    пишет файлы с нуля (текущие файлы шага, которых нет в ответе, остаются
    на диске как есть).

    `resolve_artifact_path(filename)` — как разложить `artifacts` ответа
    модели по каталогам `problems/<problem_id>/...` (у каждого шага своя
    раскладка, см. CLAUDE.md, "Структура каталогов").

    `extra_context_documents` — дополнительные пути (относительно корня
    репозитория), специфичные для конкретного вызова этого шага, а не для
    всех его вызовов вообще (файлы референсов спека, см.
    `orchestrator/references.py` и `load_context_documents`). Вместе с фиксированным списком
    `STEP_CONTEXT_DOCUMENTS[step_name]` они читаются и передаются модели как
    `context_documents`, а также входят в `compute_prompt_hash` — правка
    любого из этих документов должна инвалидировать кэш шага.

    `validate_artifacts(artifacts)` — необязательная проверка набора
    артефактов из ответа модели (например, что имена файлов ровно те, что
    ожидает шаг); в режиме доработки — итогового набора файлов шага
    (изменённые + `unchanged` + переименованные, без удалённых). Вызывается
    только при `confirmed`/`proposed` (при
    `uncertain` `artifacts` по контракту может быть пустым/частичным) и
    строго ДО записи на диск: если она бросает `ValueError`, из ответа не
    применяется ничего — всё уходит в конфликты (см. докстринг модуля), а
    сохранённый ответ остаётся для `llm apply`. `MissingArtifactsError`
    (неполный ответ) после вызова модели — один дозапрос недостающих файлов
    (`MISSING_ARTIFACTS_PROMPT`), они добавляются к ответу (заметка
    `retried`), и проверка повторяется; у сохранённого ответа (`llm apply`)
    дозапроса нет.

    `generated_artifacts` — файлы шага, которые пишет оркестратор, а не
    модель (`GeneratedArtifacts`).

    `partial_apply` — файлы шага независимы друг от друга: при конфликте в
    части файлов остальные применяются. `False` — при любом конфликте ответ
    не применяется целиком.

    `postprocess_artifacts(artifacts)` — необязательная механическая
    доработка артефактов после `validate_artifacts` и до записи на диск:
    возвращает новый набор артефактов и заметки о сделанном (добавляются к
    `notes` ответа модели, чтобы автор видел, что файл правила не модель).
    В режиме доработки получает только новые и изменённые файлы.

    Ответ модели (кроме `uncertain` — применять нечего) сохраняется
    (`cache.PendingResponse`) до проверок ответа и записи файлов и удаляется,
    когда применён (хотя бы частично): если ответ не применён целиком, вызов
    не потерян. `saved_response` — такой
    сохранённый ответ: применяется вместо вызова модели (`llm apply`);
    совпадение его входа с текущим и режим доработки проверяет вызывающий
    код (`pipeline.py`).

    При `status: uncertain` бросает `StepUncertainError` и не пишет ничего в
    `problems/`. При `confirmed`/`proposed` пишет артефакты, компилирует все
    записанные `.cpp`-файлы через `compile_check` (без запуска — см.
    CLAUDE.md, "Минимальная страховка вместо судящего контура") и сохраняет
    `CacheEntry` с этим `input_hash`, актуальным хешем промптов, моделью
    шага из `model_router.STEP_TO_MODEL` и каталогом конфликтов, если они
    есть.
    """
    input_values = input_values or {}
    if saved_response is None:
        system_prompt = read_system_prompt(step_name, prompts_dir=prompts_dir)
        user_prompt = render_user_prompt(step_name, context, prompts_dir=prompts_dir)
        if revision is not None:
            user_prompt = user_prompt.rstrip("\n") + "\n\n" + render_revision_prompt(
                revision, input_values, prompts_dir=prompts_dir
            )
        context_documents = load_context_documents(
            step_name, templates_dir=templates_dir, extra_paths=extra_context_documents
        )
        response = model_router.call_model(
            step_name, system_prompt, user_prompt, context_documents=context_documents
        )
    else:
        response = saved_response

    prompt_kwargs = dict(
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents,
    )
    cache_entry = CacheEntry(
        input_hash=input_hash,
        prompt_hash=compute_prompt_hash(step_name, **prompt_kwargs),
        model=model_router.STEP_TO_MODEL[step_name],
        status=response.status,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        input_parts=input_parts_from_values(input_values),
        prompt_parts=compute_prompt_parts(step_name, **prompt_kwargs),
        input_values=input_values,
    )

    def check_status(response: ModelResponse) -> None:
        if response.status not in _VALID_STATUSES:
            raise ValueError(
                f"Step '{step_name}': the model returned an unknown status "
                f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
            )
        if response.status == STATUS_UNCERTAIN:
            # Применять нечего; прежний неприменённый ответ того же шага устарел.
            discard_pending_response(problem_id, step_name, problems_dir=problems_dir)
            raise StepUncertainError(step_name, problem_id, response.notes)

    def plan_and_check(response: ModelResponse) -> tuple[_ApplyPlan, Optional[str], list[str]]:
        return _plan_and_check(
            response,
            revision,
            resolve_artifact_path,
            generated_artifacts,
            validate_artifacts,
            partial_apply,
        )

    def save_pending(response: ModelResponse) -> None:
        save_pending_response(
            problem_id,
            step_name,
            PendingResponse(
                cache_entry=cache_entry, revised=revision is not None, response=asdict(response)
            ),
            problems_dir=problems_dir,
        )

    check_status(response)
    if saved_response is None:
        save_pending(response)
    plan, whole_step_reason, missing = plan_and_check(response)
    if missing and saved_response is None:
        # Неполный ответ: недостающие файлы — одним дозапросом, а не новым
        # вызовом целиком (`MISSING_ARTIFACTS_PROMPT`).
        retry_prompt = user_prompt.rstrip("\n") + "\n\n" + render_missing_artifacts_prompt(
            missing, response.artifacts, prompts_dir=prompts_dir
        )
        retry = model_router.call_model(
            step_name, system_prompt, retry_prompt, context_documents=context_documents
        )
        response = _merge_missing_artifacts(response, retry, missing, resolve_artifact_path)
        cache_entry.status = response.status
        check_status(response)
        save_pending(response)
        plan, whole_step_reason, _ = plan_and_check(response)

    notes = list(response.notes)
    notes.extend(plan.notes)

    artifacts = plan.artifacts
    if postprocess_artifacts is not None and artifacts:
        artifacts, extra_notes = postprocess_artifacts(artifacts)
        notes.extend(extra_notes)
    if generated_artifacts is not None and whole_step_reason is None:
        artifacts = {**artifacts, **generated_artifacts.artifacts}
        plan.removed.extend(
            (path, "no longer produced from the spec")
            for path in generated_artifacts.stale.values()
        )

    problem_dir = Path(problems_dir) / problem_id
    backup = _Backup(problem_dir)
    marks = _apply_removals_and_renames(plan, backup)
    renamed = plan.renamed
    artifact_paths = _write_artifacts(
        artifacts,
        resolve_artifact_path,
        backup,
        skip_backup={new_path for _, new_path, _ in renamed},
    )
    conflicts_dir: Optional[Path] = None
    if plan.conflicts:
        conflicts_dir = _write_conflicts(
            step_name, plan.conflicts, problem_dir, resolve_artifact_path, whole_step_reason
        )
        cache_entry.conflicts = conflicts_dir.relative_to(problem_dir).as_posix()

    to_compile = dict(artifact_paths)
    for _, new_path, _ in renamed:
        to_compile.setdefault(new_path.name, new_path)
    if any(path.suffix == ".cpp" for path in to_compile.values()):
        ensure_problem_lib(problem_id, problems_dir=problems_dir, templates_dir=templates_dir)
    compile_results: dict[str, CompileResult] = {
        # `problem_lib.h` задачи — раньше шаблонного.
        filename: compile_check(
            path, [Path(problems_dir) / problem_id, Path(templates_dir)]
        )
        for filename, path in to_compile.items()
        if path.suffix == ".cpp"
    }

    cache_entry.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_cache_entry(problem_id, step_name, cache_entry, problems_dir=problems_dir)
    if whole_step_reason is None:
        # Не применённый целиком ответ остаётся: после правки причины
        # `llm apply` применит его без нового вызова модели.
        discard_pending_response(problem_id, step_name, problems_dir=problems_dir)

    return StepResult(
        step_name=step_name,
        problem_id=problem_id,
        status=response.status,
        artifact_paths=artifact_paths,
        notes=notes,
        compile_results=compile_results,
        revised=revision is not None,
        removed=marks,
        renamed=renamed,
        backup_dir=backup.directory,
        conflicts=[(conflict.filename, conflict.reason) for conflict in plan.conflicts],
        conflicts_dir=conflicts_dir,
        not_applied_reason=whole_step_reason,
    )
