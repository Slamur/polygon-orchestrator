"""Адрес задачи и её `problem_id`.

Задачи лежат в двух корнях — `problems/` (задачи автора) и `examples/`
(учебные задачи-образцы) — на любой глубине вложенности: промежуточные
каталоги только группируют задачи (`problems/<контест>/<год>/<раунд>/...`).
Каталог задачи — первый каталог на пути вниз от корня, в котором есть
`spec.yaml` или `polygon_state.json` (`PROBLEM_MARKERS`); внутрь каталога
задачи поиск не спускается, задачи друг в друга не вкладываются.

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

SPEC_FILENAME = "spec.yaml"
POLYGON_STATE_FILENAME = "polygon_state.json"
PROBLEM_MARKERS = (SPEC_FILENAME, POLYGON_STATE_FILENAME)

EXAMPLE_ID_SEPARATOR = "--"

# Компонент пути задачи: ASCII-буквы/цифры/'-'/'_', без '-' по краям — иначе
# склейка компонентов примеров через `--` перестала бы быть однозначной.
_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?$")
# `problem_id` спека и имя задачи на Polygon.
PROBLEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


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
    return any((Path(path) / marker).is_file() for marker in PROBLEM_MARKERS)


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
