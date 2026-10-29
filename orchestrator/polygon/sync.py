"""Инкрементальная синхронизация с Polygon: что считается "тем же самым".

`polygon_state.json` хранит базу синхронизации (как `origin/...` в git):

- `revision` — ревизия задачи на Polygon, с которой локальная база
  последний раз совпадала (после коммита `polygon push` или после
  `polygon pull`); `None` — неизвестна (задача создана/привязана до
  появления этого поля или ещё ни разу не синхронизировалась);
- `synced` — ключ элемента задачи -> sha256 его содержимого в этой ревизии.

`polygon push` загружает только элементы, чей текущий локальный хеш
отличается от `synced` — и только если ревизия на Polygon всё ещё равна
`revision`, а в рабочей копии нет незакоммиченных правок (иначе Polygon
меняли в обход оркестратора — см. `orchestrator/polygon/pipeline.py`). `polygon pull`
переписывает базу по тому, что сейчас на Polygon.

Ключ — путь файла относительно `problems/<problem_id>/` (или
`templates/` для `problem_lib.h`), кроме нескольких "не-файлов":
TL/ML, имя назначенного валидатора/чекера, название задачи в условии.
Хеш всегда считается от того, что лежит/легло бы локально, — поэтому
`push` и `pull` получают одинаковые значения для одинакового содержимого.

`stamps` — для файлов, которые Polygon отдаёт листингом с временем
изменения (`problem.files`, `problem.solutions`; см. `remote_stamps`):
ключ -> отметка той версии файла на Polygon, которой соответствует хеш
ключа в `synced`. Совпала отметка в листинге — файл на Polygon с тех пор не
менялся, и `polygon pull` не скачивает его, а берёт хеш из базы. Отметку
записывает pull (вместе с хешем) и push в конце прогона (`pipeline.py`).
Лишнее несовпадение (файл пересохранили без изменений) стоит только
скачивания; совпадения при изменённом файле не бывает, пока Polygon
обновляет время изменения при каждой правке.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    CONSTRAINTS_FILENAME,
    SPEC_FILENAME,
    TESTS_PLAN_FILENAME,
    is_authoring_name,
    is_draft_name,  # noqa: F401 — часть интерфейса модуля (pull, upload_attachments)
)

LIMITS_KEY = "limits"
PROBLEM_LIB_KEY = "problem_lib.h"
VALIDATOR_FILE_KEY = "validator.cpp"
VALIDATOR_KEY = "validator"  # имя файла, назначенного валидатором
CHECKER_FILE_KEY = "checker.cpp"
CHECKER_KEY = "checker"  # имя назначенного чекера (`checker.cpp` или `std::...`)
INTERACTIVE_KEY = "interactive"  # флаг интерактивности задачи (`problem.updateInfo`)
INTERACTOR_FILE_KEY = "interactor.cpp"
INTERACTOR_KEY = "interactor"  # имя назначенного интерактора
STATEMENT_NAME_KEY = "statement/name"
SCRIPT_KEY = "test_script"  # один на testset, какой бы локальный файл ни был
# Attachments — файлы `authoring/` каталога задачи; на Polygon и в базе — по
# имени файла, без каталога.
SPEC_KEY = SPEC_FILENAME
CONSTRAINTS_KEY = CONSTRAINTS_FILENAME  # файл целиком; TL/ML задачи — `LIMITS_KEY`
TESTS_PLAN_KEY = TESTS_PLAN_FILENAME

# Заголовок `constraints.yaml`, который писали прежние версии `polygon pull`,
# когда на Polygon есть только TL/ML (теперь TL/ML — в `limits.yaml`): такой
# файл — не результат `constraints_pick`, и `upload_attachments` его не
# загружает.
PULLED_CONSTRAINTS_HEADER = (
    "# Pulled from Polygon (orchestrator <problem> polygon pull): TL/ML only.\n"
    "# This is NOT a constraints_pick result — there are no variable ranges,\n"
    "# test groups or validator section here. Steps that take constraints.yaml\n"
    "# as input (tests_plan and the steps after it) will see only this.\n"
)

# Локальные имена валидатора, чекера и интерактора: source-файлы с такими
# именами — не генераторы, даже если не назначены (см. `generator_source_names`).
VALIDATOR_FILENAME = "validator.cpp"
CHECKER_FILENAME = "checker.cpp"
INTERACTOR_FILENAME = "interactor.cpp"
STANDARD_CHECKER_PREFIX = "std::"


def is_attachment_name(name: str) -> bool:
    """Aux-файл на Polygon, который хранит файл `authoring/` каталога задачи
    (шаг `upload_attachments`): спек, `constraints.yaml`, `tests_plan.yaml`,
    копия черновика.
    Ключ в базе синхронизации — само имя."""
    return is_authoring_name(name)


def statement_file_key(filename: str) -> str:
    return f"statement/{filename}"


def example_key(number: int) -> str:
    return f"statement/examples/example_{number}.txt"


def dialogue_keys(number: int) -> tuple[str, str]:
    """Ключи диалога примера интерактивной задачи (input/output for statement)."""
    return (
        f"statement/examples/example_{number}.input_for_statement.txt",
        f"statement/examples/example_{number}.output_for_statement.txt",
    )


def verify_key(number: int) -> str:
    """Ключ флага "verify input/output for statements" примера."""
    return f"statement/examples/example_{number}.verify_for_statement.txt"


def interactive_digest(interactive: bool) -> str:
    return digest(f"interactive={str(interactive).lower()}")


def generator_key(name: str) -> str:
    return f"generators/{name}"


def solution_key(name: str) -> str:
    return f"solutions/{name}"


def generator_source_names(
    source_files: list[dict],
    validator: str | None,
    checker: str | None,
    interactor: str | None = None,
) -> list[str]:
    """Имена генераторов среди source-файлов задачи на Polygon. Эвристика:
    Polygon не различает генераторы и прочие source-файлы, поэтому
    генератор — любой `*.cpp`, кроме валидатора, чекера и интерактора
    (назначенных или с локальными именами `validator.cpp`/`checker.cpp`/
    `interactor.cpp`)."""
    not_generators = {
        VALIDATOR_FILENAME,
        CHECKER_FILENAME,
        INTERACTOR_FILENAME,
        validator,
        checker,
        interactor,
    }
    return sorted(
        file["name"]
        for file in source_files
        if file["name"].endswith(".cpp") and file["name"] not in not_generators
    )


# Ключи-"не-файлы" -> как их показывать автору (`describe_keys`).
_KEY_LABELS = {
    LIMITS_KEY: "TL/ML (limits.yaml)",
    VALIDATOR_KEY: "validator assignment",
    CHECKER_KEY: "checker assignment",
    INTERACTOR_KEY: "interactor assignment",
    INTERACTIVE_KEY: "interactive flag",
    STATEMENT_NAME_KEY: "statement/name.txt",
    SCRIPT_KEY: "test script",
}
# Назначение -> файл, при загрузке которого назначение отдельно не показывается.
_ASSIGNED_FILES = {
    VALIDATOR_KEY: VALIDATOR_FILE_KEY,
    CHECKER_KEY: CHECKER_FILE_KEY,
    INTERACTOR_KEY: INTERACTOR_FILE_KEY,
}


def describe_keys(keys: Iterable[str]) -> list[str]:
    """Ключи базы -> имена для вывода CLI, по порядку ключей: файлы — путём в
    каталоге задачи (Attachments — с `authoring/`), остальное — словами.
    Назначение валидатора/чекера/интерактора вместе с его файлом — одна
    строка, файл."""
    keys = list(keys)
    return [
        _KEY_LABELS.get(key) or (f"{AUTHORING_DIRNAME}/{key}" if is_attachment_name(key) else key)
        for key in keys
        if _ASSIGNED_FILES.get(key) not in keys
    ]


def is_stamped_key(key: str) -> bool:
    """Есть ли у ключа отметка в листинге Polygon (см. `remote_stamps`)."""
    return key in (
        PROBLEM_LIB_KEY,
        VALIDATOR_FILE_KEY,
        CHECKER_FILE_KEY,
        INTERACTOR_FILE_KEY,
    ) or is_attachment_name(key) or key.startswith(("generators/", "solutions/"))


def file_stamp(file: dict) -> str | None:
    """Отметка версии файла/решения на Polygon (объекты `File`/`Solution`):
    имя, время изменения, длина. `None` — Polygon не отдал время."""
    mtime = file.get("modificationTimeSeconds")
    if mtime is None:
        return None
    return f"{file['name']}:{mtime}:{file.get('length')}"


def remote_stamps(
    files: dict,
    solutions: list[dict],
    validator: str | None,
    checker: str | None,
    interactor: str | None = None,
) -> dict[str, str]:
    """Ключ базы -> отметка текущей версии на Polygon, по ответам
    `problem.files`, `problem.solutions`, `problem.validator`,
    `problem.checker`, `problem.interactor` (только у интерактивной задачи)
    — для всех файлов, которые сравнивает pull."""
    sources = {file["name"]: file for file in files.get("sourceFiles") or []}
    by_key: dict[str, dict] = {}
    for file in files.get("resourceFiles") or []:
        if file["name"] == PROBLEM_LIB_KEY:
            by_key[PROBLEM_LIB_KEY] = file
    for file in files.get("auxFiles") or []:
        if is_attachment_name(file["name"]):
            by_key[file["name"]] = file
    if validator in sources:
        by_key[VALIDATOR_FILE_KEY] = sources[validator]
    if checker in sources and not checker.startswith(STANDARD_CHECKER_PREFIX):
        by_key[CHECKER_FILE_KEY] = sources[checker]
    if interactor in sources:
        by_key[INTERACTOR_FILE_KEY] = sources[interactor]
    for name in generator_source_names(list(sources.values()), validator, checker, interactor):
        by_key[generator_key(name)] = sources[name]
    for solution in solutions:
        by_key[solution_key(solution["name"])] = solution
    stamps = {key: file_stamp(file) for key, file in by_key.items()}
    return {key: stamp for key, stamp in stamps.items() if stamp is not None}


def fetch_remote_stamps(
    client: PolygonClient, polygon_id: int, *, interactive: bool = False
) -> dict[str, str]:
    """`remote_stamps` для задачи `polygon_id` — четыре запроса, у
    интерактивной задачи пять (`problem.interactor` у обычной задачи не
    запрашивается: интерактора у неё нет)."""
    params = {"problemId": str(polygon_id)}
    return remote_stamps(
        client.call("problem.files", params) or {},
        client.call("problem.solutions", params) or [],
        client.call("problem.validator", params),
        client.call("problem.checker", params),
        client.call("problem.interactor", params) if interactive else None,
    )


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def limits_digest(time_limit_ms: int, memory_limit_mb: int) -> str:
    return digest(f"timeLimit={time_limit_ms};memoryLimit={memory_limit_mb}")


@dataclass(frozen=True)
class PolygonRevision:
    """Ревизия задачи на Polygon (поля объекта `Problem`)."""

    revision: int
    modified: bool  # есть незакоммиченные правки в рабочей копии


def fetch_revision(client: PolygonClient, polygon_id: int) -> PolygonRevision:
    """Текущая ревизия задачи `polygon_id` — один вызов `problems.list`."""
    found = [
        item
        for item in client.call("problems.list", {"id": str(polygon_id)}) or []
        if item.get("id") == polygon_id
    ]
    if not found:
        raise PolygonStepError(f"Polygon problem id={polygon_id} is not available to this API key")
    problem = found[0]
    return PolygonRevision(
        revision=int(problem.get("revision", 0)), modified=bool(problem.get("modified"))
    )
