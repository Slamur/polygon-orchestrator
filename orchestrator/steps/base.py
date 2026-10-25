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
   `confirmed`/`proposed` — записать артефакты в `problems/<problem_id>/...`,
   прогнать `compile_check` по всем `.cpp`-артефактам и сохранить
   `CacheEntry` через `cache.py`.

Режим доработки (`StepRevision`): если файлы шага уже есть на диске, модель
получает их вместе с тем, что изменилось во входе шага с прошлого прогона
(`prompts/revise.md.j2`), и дорабатывает их, а не пишет с нуля. Каждый
текущий файл в ответе — ровно в одном из: `artifacts` (изменён),
`unchanged`, `removed`, `renamed[].from` (`_plan_revision`). Удалённые
файлы не удаляются, а переименовываются в `<имя>.removed`
(`REMOVED_SUFFIX`) — автор посмотрит и удалит сам; переименования
применяются сразу. Прежние версии перезаписанных и переименованных файлов
(в любом режиме) — в `.llm_backup/<время>/` каталога задачи
(`problems/` вне git, истории у файлов задачи нет).

Этот модуль реализует шаги 1-5 один раз (`run_generative_step`); конкретные
модули в `orchestrator/steps/*.py` только знают, какую секцию спека читать,
как разложить артефакты по каталогам `problems/<id>/...` и что входит в
апстрим-хеш — сами не дублируют этот цикл.
"""

from __future__ import annotations

import datetime
import json
import shutil
from dataclasses import asdict, dataclass, field
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
from orchestrator.problem_paths import EXAMPLES_DIR, PROBLEMS_DIR, REMOVED_SUFFIX, is_removed_mark

PROMPTS_DIR = Path("prompts")
TEMPLATES_DIR = Path("templates")

# `REVISE_PROMPT` (импортирован из `cache.py`) — общий для всех шагов блок
# user-промпта режима доработки (относительно prompts_dir): дописывается к
# `prompts/<step>/user.md.j2`, когда модели показаны текущие файлы шага.

# Каталог (в каталоге задачи) с прежними версиями файлов, которые шаг
# перезаписал или переименовал: `.llm_backup/<время>/<путь в каталоге задачи>`.
LLM_BACKUP_DIRNAME = ".llm_backup"
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
        "tutorials/limits.md",
    ],
    "checker_draft": [
        "tutorials/checker.md",
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
    (`examples/<problem_id>/validator.cpp`, `problems/<problem_id>/validator.cpp`).
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
class _RevisionPlan:
    """Проверенный ответ модели в режиме доработки — что сделать с файлами."""

    # Итоговый набор файлов шага: имя -> содержимое (то, что останется после
    # применения ответа; его проверяет `validate_artifacts`).
    final_artifacts: dict[str, str]
    removed: list[tuple[Path, str]]
    renamed: list[tuple[Path, Path, str]]


def _plan_revision(
    step_name: str,
    response: Any,
    revision: StepRevision,
    resolve_path: Callable[[str], Path],
) -> _RevisionPlan:
    """Проверяет, что каждый текущий файл шага в ответе модели ровно в
    одном из `artifacts`/`unchanged`/`removed`/`renamed[].from`, а новые
    имена переименований свободны. Файлы сопоставляются по пути
    (`resolve_path`), а не по строке имени: `generators/gen.cpp` и `gen.cpp`
    — один файл. Любая проблема — `ValueError` со всеми проблемами сразу, до
    записи на диск."""
    current_by_path = {path.resolve(): name for name, path in revision.current_paths.items()}
    problems: list[str] = []
    seen: dict[str, list[str]] = {name: [] for name in revision.current_paths}

    def current_name(filename: str, where: str) -> Optional[str]:
        name = current_by_path.get(resolve_path(filename).resolve())
        if name is None:
            problems.append(f"{where} '{filename}' is not a current file of the step")
        else:
            seen[name].append(where)
        return name

    for filename in response.artifacts:
        name = current_by_path.get(resolve_path(filename).resolve())
        if name is not None:
            seen[name].append("artifacts")
    unchanged = [
        name for filename in response.unchanged
        if (name := current_name(filename, "unchanged")) is not None
    ]
    removed: list[tuple[Path, str]] = []
    for entry in response.removed:
        name = current_name(entry["filename"], "removed")
        if name is not None:
            removed.append((revision.current_paths[name], entry["reason"]))
    renamed: list[tuple[Path, Path, str]] = []
    targets: set[Path] = set()
    for entry in response.renamed:
        name = current_name(entry["from"], "renamed.from")
        target = resolve_path(entry["to"])
        if target.resolve() in current_by_path or target.exists():
            problems.append(f"renamed.to '{entry['to']}' is already taken")
        elif target.resolve() in targets:
            problems.append(f"renamed.to '{entry['to']}' is used twice")
        targets.add(target.resolve())
        if name is not None:
            renamed.append((revision.current_paths[name], target, entry["reason"]))

    for name, places in seen.items():
        if not places:
            problems.append(f"current file '{name}' is missing from the response")
        elif len(places) > 1:
            problems.append(f"current file '{name}' is in several places: {', '.join(places)}")
    if problems:
        raise ValueError(
            f"Step '{step_name}': the revision response does not account for the "
            f"current files ({'; '.join(problems)})"
        )

    final_artifacts = {
        name: revision.current_paths[name].read_text(encoding="utf-8") for name in unchanged
    }
    for entry, (old_path, _, _) in zip(response.renamed, renamed):
        final_artifacts[entry["to"]] = old_path.read_text(encoding="utf-8")
    final_artifacts.update(response.artifacts)
    return _RevisionPlan(final_artifacts=final_artifacts, removed=removed, renamed=renamed)


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


def _apply_removals_and_renames(plan: _RevisionPlan, backup: _Backup) -> list[tuple[Path, str]]:
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
    изменениями входа; ответ проверяется `_plan_revision`, удалённые файлы
    помечаются `.removed`, переименования применяются. `None` — модель
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
    строго ДО записи на диск: если она бросает `ValueError`, исключение
    всплывает из этой функции как есть, в `problems/` не пишется ни один
    файл и `CacheEntry` не сохраняется.

    `postprocess_artifacts(artifacts)` — необязательная механическая
    доработка артефактов после `validate_artifacts` и до записи на диск:
    возвращает новый набор артефактов и заметки о сделанном (добавляются к
    `notes` ответа модели, чтобы автор видел, что файл правила не модель).
    В режиме доработки получает только новые и изменённые файлы.

    Ответ модели (кроме `uncertain` — применять нечего) сохраняется
    (`cache.PendingResponse`) до проверок ответа и записи файлов и удаляется,
    когда применён: если проверка ответа упала, вызов не потерян. `saved_response` — такой
    сохранённый ответ: применяется вместо вызова модели (`llm apply`);
    совпадение его входа с текущим и режим доработки проверяет вызывающий
    код (`pipeline.py`).

    При `status: uncertain` бросает `StepUncertainError` и не пишет ничего в
    `problems/`. При `confirmed`/`proposed` пишет артефакты, компилирует все
    записанные `.cpp`-файлы через `compile_check` (без запуска — см.
    CLAUDE.md, "Минимальная страховка вместо судящего контура") и сохраняет
    `CacheEntry` с этим `input_hash`, актуальным хешем промптов и моделью
    шага из `model_router.STEP_TO_MODEL`.
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

    if response.status not in _VALID_STATUSES:
        raise ValueError(
            f"Step '{step_name}': the model returned an unknown status "
            f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
        )

    if response.status == STATUS_UNCERTAIN:
        # Применять нечего; прежний неприменённый ответ того же шага устарел.
        discard_pending_response(problem_id, step_name, problems_dir=problems_dir)
        raise StepUncertainError(step_name, problem_id, response.notes)

    if saved_response is None:
        save_pending_response(
            problem_id,
            step_name,
            PendingResponse(
                cache_entry=cache_entry, revised=revision is not None, response=asdict(response)
            ),
            problems_dir=problems_dir,
        )

    notes = list(response.notes)
    plan: Optional[_RevisionPlan] = None
    if revision is not None:
        plan = _plan_revision(step_name, response, revision, resolve_artifact_path)
    elif response.unchanged or response.removed or response.renamed:
        notes.append({
            "field": "response",
            "kind": "ignored",
            "explanation": (
                "модель вернула unchanged/removed/renamed, хотя текущие файлы ей "
                "не показывались (генерация с нуля), — проигнорировано"
            ),
        })

    if validate_artifacts is not None:
        validate_artifacts(plan.final_artifacts if plan is not None else response.artifacts)

    artifacts = response.artifacts
    if postprocess_artifacts is not None:
        artifacts, extra_notes = postprocess_artifacts(artifacts)
        notes.extend(extra_notes)

    backup = _Backup(Path(problems_dir) / problem_id)
    marks: list[tuple[Path, str]] = []
    renamed: list[tuple[Path, Path, str]] = []
    if plan is not None:
        marks = _apply_removals_and_renames(plan, backup)
        renamed = plan.renamed
    artifact_paths = _write_artifacts(
        artifacts,
        resolve_artifact_path,
        backup,
        skip_backup={new_path for _, new_path, _ in renamed},
    )

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
    )
