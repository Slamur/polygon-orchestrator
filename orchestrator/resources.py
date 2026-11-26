"""Resource files: what a build of the problem's sources includes, as Polygon's "Resource
files" of a problem (`problem_lib.h`, `testlib.h`, later the statement's LaTeX files).

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

# The directories of the default resources a C++ build includes, after the problem's.
_DEFAULT_INCLUDE_DIRS = (TESTLIB_DIR,)


def problem_resources_dir(problem_dir: Path) -> Path:
    return Path(problem_dir) / PROBLEM_RESOURCES_DIRNAME


def include_dirs(problem_dir: Path) -> list[Path]:
    """`-I` directories of a C++ build of the problem's sources, in lookup order."""
    return [problem_resources_dir(problem_dir), *_DEFAULT_INCLUDE_DIRS]


def resource_path(problem_dir: Path, name: str) -> Path | None:
    """The version of the resource `name` a build of the problem uses: the problem's own,
    else the default; `None` — neither exists."""
    for directory in include_dirs(problem_dir):
        path = directory / name
        if path.is_file():
            return path
    return None
