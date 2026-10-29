"""Адрес задачи и её `problem_id`.

Задачи лежат в двух корнях — `problems/` (задачи автора) и `examples/`
(учебные задачи-образцы) — на любой глубине вложенности: промежуточные
каталоги только группируют задачи (`problems/<контест>/<год>/<раунд>/...`).
Каталог задачи — первый каталог на пути вниз от корня, в котором есть
`authoring/spec.yaml` или `polygon_state.json` (`PROBLEM_MARKERS`); внутрь
каталога задачи поиск не спускается, задачи друг в друга не вкладываются.

Файлы, с которыми работает только модель и автор, — спек, результат
`constraints_pick` и копия черновика (`AUTHORING_FILES`) — лежат в подкаталоге
`authoring/` каталога задачи; всё остальное (условие, валидатор, генераторы,
`limits.yaml`, ...) — в самом каталоге задачи, как на Polygon. Задачи,
созданные до `authoring/`, переносит `migrate_to_authoring`.

Адрес задачи — путь её каталога вместе с корнем, так он и пишется в CLI:
`problems/contest/2026/round-1/my-task`, `examples/array/prefix-sums`.
Внутри оркестратора адрес передаётся парой: `problems_dir` — корень
(`problems/` или `examples/`), аргумент `problem_id` функций — путь каталога
задачи внутри корня (у задачи прямо в корне он совпадает с её `problem_id`).

`problem_id` задачи — поле `problem_id` спека и имя задачи на Polygon —
выводится из пути (`problem_id_for`):
- в `problems/` — имя самого вложенного каталога
  (`contest/2026/round-1/my-task` -> `my-task`);
- в `examples/` — все компоненты пути через `--`
  (`array/prefix-sums` -> `array--prefix-sums`), поэтому компоненты пути
  примеров не могут содержать `--`.

`problem_id` уникален среди всех задач обоих корней (у Polygon одно
пространство имён) — это проверяет `check_new_problem_location` при
создании каталога задачи (`init`, `polygon pull`).

Модуль без тяжёлых импортов: им пользуется Tab-дополнение в `cli.py`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterator

PROBLEMS_DIR = Path("problems")
EXAMPLES_DIR = Path("examples")
PROBLEM_ROOTS = (PROBLEMS_DIR, EXAMPLES_DIR)

AUTHORING_DIRNAME = "authoring"
SPEC_FILENAME = "spec.yaml"
CONSTRAINTS_FILENAME = "constraints.yaml"
# Результат шага `tests_plan` — план генераторов и тестов.
TESTS_PLAN_FILENAME = "tests_plan.yaml"
# Копия черновика автора (`init --draft`): `draft<расширение>`, см.
# `orchestrator/drafts.py`.
DRAFT_STEM = "draft"
POLYGON_STATE_FILENAME = "polygon_state.json"
# `spec.yaml` прямо в каталоге задачи — задача, ещё не перенесённая в
# `authoring/` (`migrate_to_authoring`): её тоже находят дополнение и
# проверка уникальности `problem_id`.
PROBLEM_MARKERS = (
    f"{AUTHORING_DIRNAME}/{SPEC_FILENAME}",
    SPEC_FILENAME,
    POLYGON_STATE_FILENAME,
)

EXAMPLE_ID_SEPARATOR = "--"

# Суффикс файла задачи, который генеративный шаг в режиме доработки
# предложил удалить (`orchestrator/steps/base.py`): файл не удаляется, а
# переименовывается в `<имя>.removed`, чтобы автор посмотрел и удалил сам.
# Такие файлы — уже не часть задачи: их не видят ни шаги, ни Polygon-шаги,
# ни референсы (`is_removed_mark`).
REMOVED_SUFFIX = ".removed"


def is_removed_mark(path: Path | str) -> bool:
    """Файл помечен шагом как удалённый (`REMOVED_SUFFIX`)."""
    return Path(path).name.endswith(REMOVED_SUFFIX)


# Компонент пути задачи: ASCII-буквы/цифры/'-'/'_', без '-' по краям — иначе
# склейка компонентов примеров через `--` перестала бы быть однозначной.
_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?$")
# `problem_id` спека и имя задачи на Polygon.
PROBLEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def is_draft_name(name: str) -> bool:
    """Копия черновика автора (`draft<расширение>`)."""
    return "/" not in name and PurePosixPath(name).stem == DRAFT_STEM


def is_authoring_name(name: str) -> bool:
    """Файл, который лежит в `authoring/`: спек, `constraints.yaml`,
    `tests_plan.yaml`, копия черновика (по имени файла, без каталога)."""
    return name in (SPEC_FILENAME, CONSTRAINTS_FILENAME, TESTS_PLAN_FILENAME) or is_draft_name(
        name
    )


def authoring_dir(problem_dir: Path) -> Path:
    return Path(problem_dir) / AUTHORING_DIRNAME


def authoring_path(problem_dir: Path, name: str) -> Path:
    """Путь файла `authoring/` (`is_authoring_name`) в каталоге задачи."""
    return authoring_dir(problem_dir) / name


def find_authoring_file(problem_dir: Path, name: str) -> Path:
    """Файл `authoring/` другой задачи, которую читают как образец (`--ref`,
    референсы спека): та задача может быть ещё не перенесена
    (`migrate_to_authoring`) — тогда файл берётся из самого её каталога."""
    path = authoring_path(problem_dir, name)
    legacy = Path(problem_dir) / name
    return legacy if not path.exists() and legacy.exists() else path


def migrate_to_authoring(problem_dir: Path) -> list[tuple[Path, Path]]:
    """Переносит спек, `constraints.yaml` и копию черновика из самого каталога
    задачи (раскладка до `authoring/`) в `authoring/`; [(откуда, куда), ...].
    Файл, который есть в обоих местах, — `ProblemPathError` до любого
    переноса: какой из двух актуален, решает автор."""
    problem_dir = Path(problem_dir)
    if not problem_dir.is_dir():
        return []
    moves = [
        (path, authoring_path(problem_dir, path.name))
        for path in sorted(problem_dir.iterdir())
        if path.is_file() and is_authoring_name(path.name)
    ]
    clashes = [str(source) for source, target in moves if target.exists()]
    if clashes:
        raise ProblemPathError(
            f"{', '.join(clashes)}: the same file is also in {authoring_dir(problem_dir)} — "
            "keep the actual one in authoring/ and delete the other"
        )
    for source, target in moves:
        target.parent.mkdir(exist_ok=True)
        source.rename(target)
    return moves


class ProblemPathError(ValueError):
    """Неверный адрес задачи или место для нового каталога задачи."""


def is_examples_root(problems_dir: Path) -> bool:
    """Корень учебных задач (`examples/`) — по имени каталога корня."""
    return Path(problems_dir).name == EXAMPLES_DIR.name


def problem_id_for(problems_dir: Path, problem_path: str) -> str:
    """`problem_id` задачи по её пути `problem_path` внутри корня `problems_dir`
    (правила — в докстринге модуля)."""
    parts = PurePosixPath(problem_path).parts
    if is_examples_root(problems_dir):
        return EXAMPLE_ID_SEPARATOR.join(parts)
    return parts[-1]


def problem_address(problems_dir: Path, problem_path: str) -> str:
    """Адрес задачи, как он пишется в CLI (`problems/<путь>`, `examples/<путь>`) —
    для подсказок вида "запустите `orchestrator <адрес> ...`"."""
    return f"{Path(problems_dir).name}/{problem_path}"


def parse_problem_address(address: str) -> tuple[Path, str]:
    """(корень, путь задачи внутри корня) по адресу из CLI. Завершающий `/`
    (его подставляет дополнение путей shell) допускается."""
    parts = [part for part in address.strip().split("/") if part]
    roots = {root.name: root for root in PROBLEM_ROOTS}
    allowed = " or ".join(f"'{name}/'" for name in roots)
    if len(parts) < 2 or parts[0] not in roots:
        raise ProblemPathError(
            f"problem address '{address}' must start with {allowed} followed by the problem "
            "directory, e.g. problems/my-task or examples/array/prefix-sums"
        )
    root, rest = roots[parts[0]], parts[1:]
    for part in rest:
        if not _COMPONENT_PATTERN.match(part):
            raise ProblemPathError(
                f"problem address '{address}': directory name '{part}' must be ASCII "
                "letters/digits/'-'/'_', not starting or ending with '-'"
            )
        if part == AUTHORING_DIRNAME:
            raise ProblemPathError(
                f"problem address '{address}': '{AUTHORING_DIRNAME}' is the subdirectory of a "
                "problem with its spec, not a problem or a group of problems"
            )
        if is_examples_root(root) and EXAMPLE_ID_SEPARATOR in part:
            raise ProblemPathError(
                f"problem address '{address}': directory names in {root.name}/ must not contain "
                f"'{EXAMPLE_ID_SEPARATOR}' — it joins them into the problem_id"
            )
    problem_path = "/".join(rest)
    problem_id = problem_id_for(root, problem_path)
    if not PROBLEM_ID_PATTERN.match(problem_id):
        raise ProblemPathError(
            f"problem address '{address}': problem_id '{problem_id}' must start with a letter"
        )
    return root, problem_path


def is_problem_dir(path: Path) -> bool:
    """`authoring/` — не задача, хотя спек в нём и подходит под маркер
    задачи, ещё не перенесённой в `authoring/`."""
    path = Path(path)
    if path.name == AUTHORING_DIRNAME:
        return False
    return any((path / marker).is_file() for marker in PROBLEM_MARKERS)


def iter_problem_dirs(directory: Path) -> Iterator[Path]:
    """Каталоги задач под `directory` (сам `directory` не в счёт); скрытые
    каталоги пропускаются, внутрь каталога задачи обход не спускается."""
    directory = Path(directory)
    for current, dirnames, _ in os.walk(directory):
        current = Path(current)
        if current != directory and is_problem_dir(current):
            dirnames.clear()
            yield current
        else:
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))


def problem_paths(problems_dir: Path) -> list[str]:
    """Пути задач внутри корня `problems_dir` (posix, отсортированы)."""
    return sorted(
        path.relative_to(problems_dir).as_posix() for path in iter_problem_dirs(problems_dir)
    )


def check_new_problem_location(problems_dir: Path, problem_path: str) -> None:
    """Проверяет, что в `problems_dir/<problem_path>` можно завести новую
    задачу: этот каталог не лежит внутри другой задачи, внутри него нет
    задач, а её `problem_id` не занят задачей в другом месте — ни в этом
    корне, ни в соседнем (`problems/` и `examples/` лежат рядом). Иначе
    `ProblemPathError`."""
    problems_dir = Path(problems_dir)
    problem_dir = problems_dir / problem_path
    for parent in PurePosixPath(problem_path).parents:
        if parent.parts and is_problem_dir(problems_dir / parent):
            outer = problem_address(problems_dir, parent.as_posix())
            raise ProblemPathError(
                f"{problem_dir} is inside another problem {outer} — problems can't be nested"
            )
    for nested in iter_problem_dirs(problem_dir):
        raise ProblemPathError(
            f"{problem_dir} already contains problem {nested} — problems can't be nested"
        )

    problem_id = problem_id_for(problems_dir, problem_path)
    sibling_roots = [problems_dir.parent / root.name for root in PROBLEM_ROOTS]
    roots = [problems_dir] + [root for root in sibling_roots if root.name != problems_dir.name]
    for root in roots:
        for other in problem_paths(root):
            if (root, other) == (problems_dir, problem_path):
                continue
            if problem_id_for(root, other) == problem_id:
                raise ProblemPathError(
                    f"problem_id '{problem_id}' is already used by "
                    f"{problem_address(root, other)} — problem ids must be unique"
                )
