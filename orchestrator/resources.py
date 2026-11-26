"""Resource files: what a build of the problem's sources includes, as Polygon's "Resource
files" of a problem (`problem_lib.h`, `testlib.h`, the statement's LaTeX files `olymp.sty`,
`problem.tex`, `statements.ftl`).

Two levels:

- `problems/<problem path>/resources/` — the problem's own resources. `problem_lib.h` is
  always here (copied from `templates/` when the problem gets its first C++ file, see
  `ensure_problem_lib` in `orchestrator/steps/base.py`); an external resource is here only
  if the problem has its own version of it (put by hand or pulled from Polygon);
- `resources/` of the repository — the defaults of the external resources, copied
  without changes from upstream at a pinned version (`resources/README.md`); they are
  tied to the orchestrator's version, so they are found next to this package, not via
  `--base-dir`.

The problem's version comes first: it overrides the default.

Sync with Polygon (`polygon pull`/`push`) treats every resource alike: pull compares a
resource on Polygon with the version the problem uses now (its own, else the default) and,
if they differ, writes Polygon's into the problem's `resources/` — from then on the problem
uses it; push uploads only the problem's own versions, and only those changed since the
sync base. So a problem that once pulled its own `testlib.h` (or `olymp.sty`) no longer
follows the default here: it lives by Polygon's version. Resources with neither version
locally aren't synced.

The statement's files are defaults like `testlib.h`, but not include directories of a C++
build: they are only looked up as defaults (`default_resource_path`).

No heavy imports: `cli.py` imports the problem layout on every Tab.
"""

from __future__ import annotations

from pathlib import Path

PROBLEM_RESOURCES_DIRNAME = "resources"

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"
TESTLIB_DIR = RESOURCES_DIR / "testlib"
TESTLIB_FILENAME = "testlib.h"
# The standard testlib checkers Polygon offers as `std::<name>.cpp`.
STD_CHECKERS_DIR = TESTLIB_DIR / "checkers"
# Polygon's files for building the statement PDF (`olymp.sty`, `problem.tex`, `statements.ftl`).
STATEMENT_RESOURCES_DIR = RESOURCES_DIR / "polygon-statement"

# The directories of the default resources a C++ build includes, after the problem's.
_DEFAULT_INCLUDE_DIRS = (TESTLIB_DIR,)
# All the directories of the default resources, in lookup order.
_DEFAULT_DIRS = (*_DEFAULT_INCLUDE_DIRS, STATEMENT_RESOURCES_DIR)


def problem_resources_dir(problem_dir: Path) -> Path:
    return Path(problem_dir) / PROBLEM_RESOURCES_DIRNAME


def include_dirs(problem_dir: Path) -> list[Path]:
    """`-I` directories of a C++ build of the problem's sources, in lookup order."""
    return [problem_resources_dir(problem_dir), *_DEFAULT_INCLUDE_DIRS]


def default_resource_path(name: str) -> Path | None:
    """The default version of the resource `name` (`resources/` of the repository);
    `None` — there is no default for it."""
    for directory in _DEFAULT_DIRS:
        path = directory / name
        if path.is_file():
            return path
    return None


def resource_path(problem_dir: Path, name: str) -> Path | None:
    """The version of the resource `name` a build of the problem uses: the problem's own,
    else the default; `None` — neither exists."""
    for directory in include_dirs(problem_dir):
        path = directory / name
        if path.is_file():
            return path
    return None
