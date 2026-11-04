"""A problem's address and its `problem_id`.

Problems lie in two roots — `problems/` (the author's problems) and `examples/`
(sample problems) — at any nesting depth: intermediate
directories only group problems (`problems/<contest>/<year>/<round>/...`).
A problem directory is the first directory on the way down from the root that contains
`authoring/spec.yaml` or `polygon_state.json` (`PROBLEM_MARKERS`); the search doesn't descend
into a problem directory, problems aren't nested in each other.

The files only the model and the author work with — the spec, the result of
`constraints_pick`, the draft copy and the tutorial draft (`is_authoring_name`) — lie in the problem directory's
`authoring/` subdirectory; everything else (statement, validator, generators,
`limits.yaml`, ...) — in the problem directory itself, as on Polygon. Problems
created before `authoring/` are moved by `migrate_to_authoring`.

A problem's address is the path of its directory together with the root, that's how it's written in the CLI:
`problems/contest/2026/round-1/my-task`, `examples/array/prefix-sums`.
Inside the orchestrator the address is passed as a pair: `problems_dir` — the root
(`problems/` or `examples/`), the `problem_id` argument of functions — the path of the problem
directory inside the root (for a problem right in the root it matches its `problem_id`).
A group's address is the same kind of path to an intermediate directory or the root itself
(`problems/contest/2026`, `examples`): the command applies to all problems
under it (`problems_under`).

A problem's `problem_id` — the spec's `problem_id` field and the problem's name on Polygon —
is derived from the path (`problem_id_for`):
- in `problems/` — the name of the innermost directory
  (`contest/2026/round-1/my-task` -> `my-task`);
- in `examples/` — all path components joined with `--`
  (`array/prefix-sums` -> `array--prefix-sums`), so the path components of
  the examples can't contain `--`.

`problem_id` is unique among all problems of both roots (Polygon has one
namespace) — this is checked by `check_new_problem_location` when
creating a problem directory (`init`, `polygon pull`).

A module without heavy imports: Tab completion in `cli.py` uses it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

PROBLEMS_DIR = Path("problems")
EXAMPLES_DIR = Path("examples")
PROBLEM_ROOTS = (PROBLEMS_DIR, EXAMPLES_DIR)

AUTHORING_DIRNAME = "authoring"
SPEC_FILENAME = "spec.yaml"
CONSTRAINTS_FILENAME = "constraints.yaml"
# The result of the `tests_plan` step — the plan of generators and tests.
TESTS_PLAN_FILENAME = "tests_plan.yaml"
# A copy of the author's draft (`init --draft`): `draft<extension>`, see
# `orchestrator/drafts.py`.
DRAFT_STEM = "draft"
POLYGON_STATE_FILENAME = "polygon_state.json"
# The tutorial in `statement/`: an optional file, written by the author, `polygon pull` or
# the `tutorial_draft` step — from the author's draft `authoring/draft-tutorial.md`.
TUTORIAL_FILENAME = "tutorial.tex"
TUTORIAL_DRAFT_FILENAME = "draft-tutorial.md"
# `spec.yaml` directly in the problem directory — a problem not yet moved into
# `authoring/` (`migrate_to_authoring`): completion and the
# `problem_id` uniqueness check find it too.
PROBLEM_MARKERS = (
    f"{AUTHORING_DIRNAME}/{SPEC_FILENAME}",
    SPEC_FILENAME,
    POLYGON_STATE_FILENAME,
)

EXAMPLE_ID_SEPARATOR = "--"

# The suffix of a problem file that a generative step in revision mode
# proposed to delete (`orchestrator/steps/base.py`): the file isn't deleted but
# renamed to `<name>.removed`, so that the author looks and deletes it themselves.
# Such files are no longer part of the problem: neither the steps, nor the Polygon steps,
# nor references see them (`is_removed_mark`).
REMOVED_SUFFIX = ".removed"


def is_removed_mark(path: Path | str) -> bool:
    """The file is marked as deleted by a step (`REMOVED_SUFFIX`)."""
    return Path(path).name.endswith(REMOVED_SUFFIX)


# A component of a problem path: ASCII letters/digits/'-'/'_', without '-' at the ends — otherwise
# joining the examples' components with `--` would stop being unambiguous.
_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?$")
# The spec's `problem_id` and the problem's name on Polygon.
PROBLEM_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def is_draft_name(name: str) -> bool:
    """A copy of the author's draft (`draft<extension>`)."""
    return "/" not in name and PurePosixPath(name).stem == DRAFT_STEM


def is_authoring_name(name: str) -> bool:
    """A file that lies in `authoring/`: the spec, `constraints.yaml`,
    `tests_plan.yaml`, the draft copy, the tutorial draft (by file name, without
    the directory)."""
    return name in (
        SPEC_FILENAME,
        CONSTRAINTS_FILENAME,
        TESTS_PLAN_FILENAME,
        TUTORIAL_DRAFT_FILENAME,
    ) or is_draft_name(name)


def authoring_dir(problem_dir: Path) -> Path:
    return Path(problem_dir) / AUTHORING_DIRNAME


def authoring_path(problem_dir: Path, name: str) -> Path:
    """The path of an `authoring/` file (`is_authoring_name`) in the problem directory."""
    return authoring_dir(problem_dir) / name


def find_authoring_file(problem_dir: Path, name: str) -> Path:
    """An `authoring/` file of another problem that is read as a sample (`--ref`,
    spec references): that problem may not be moved yet
    (`migrate_to_authoring`) — then the file is taken from its directory itself."""
    path = authoring_path(problem_dir, name)
    legacy = Path(problem_dir) / name
    return legacy if not path.exists() and legacy.exists() else path


def migrate_to_authoring(problem_dir: Path) -> list[tuple[Path, Path]]:
    """Moves the spec, `constraints.yaml` and the draft copy from the problem directory
    itself (the layout from before `authoring/`) into `authoring/`; [(from, to), ...].
    A file that exists in both places is a `ProblemPathError` before any
    move: which of the two is current is for the author to decide."""
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
    """An invalid problem address or location for a new problem directory."""


def is_examples_root(problems_dir: Path) -> bool:
    """The root of the sample problems (`examples/`) — by the name of the root directory."""
    return Path(problems_dir).name == EXAMPLES_DIR.name


def problem_id_for(problems_dir: Path, problem_path: str) -> str:
    """The `problem_id` of a problem by its path `problem_path` inside the root `problems_dir`
    (the rules — in the module docstring)."""
    parts = PurePosixPath(problem_path).parts
    if is_examples_root(problems_dir):
        return EXAMPLE_ID_SEPARATOR.join(parts)
    return parts[-1]


def problem_address(problems_dir: Path, problem_path: str) -> str:
    """The problem's address as it's written in the CLI (`problems/<path>`, `examples/<path>`) —
    for hints like "run `orchestrator <address> ...`"."""
    return f"{Path(problems_dir).name}/{problem_path}"


def _address_root_error(address: str) -> ProblemPathError:
    allowed = " or ".join(f"'{root.name}/'" for root in PROBLEM_ROOTS)
    return ProblemPathError(
        f"problem address '{address}' must start with {allowed} followed by the problem "
        "directory, e.g. problems/my-task or examples/array/prefix-sums"
    )


def parse_address_path(address: str) -> tuple[Path, str]:
    """(root, path inside the root) by an address from the CLI — of a problem or a problem group,
    so the path may be empty (the root itself), and the name of the last directory doesn't
    have to fit `problem_id`. A trailing `/` (the shell's path completion adds it)
    is allowed."""
    parts = [part for part in address.strip().split("/") if part]
    roots = {root.name: root for root in PROBLEM_ROOTS}
    if not parts or parts[0] not in roots:
        raise _address_root_error(address)
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
    return root, "/".join(rest)


def parse_problem_address(address: str) -> tuple[Path, str]:
    """(root, the problem's path inside the root) by a problem address from the CLI."""
    root, problem_path = parse_address_path(address)
    if not problem_path:
        raise _address_root_error(address)
    problem_id = problem_id_for(root, problem_path)
    if not PROBLEM_ID_PATTERN.match(problem_id):
        raise ProblemPathError(
            f"problem address '{address}': problem_id '{problem_id}' must start with a letter"
        )
    return root, problem_path


def is_problem_dir(path: Path) -> bool:
    """`authoring/` isn't a problem, even though the spec in it fits the marker of
    a problem not yet moved into `authoring/`."""
    path = Path(path)
    if path.name == AUTHORING_DIRNAME:
        return False
    return any((path / marker).is_file() for marker in PROBLEM_MARKERS)


def iter_problem_dirs(directory: Path) -> Iterator[Path]:
    """Problem directories under `directory` (`directory` itself doesn't count); hidden
    directories are skipped, the traversal doesn't descend into a problem directory."""
    directory = Path(directory)
    for current, dirnames, _ in os.walk(directory):
        current = Path(current)
        if current != directory and is_problem_dir(current):
            dirnames.clear()
            yield current
        else:
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))


def problem_paths(problems_dir: Path) -> list[str]:
    """The paths of problems inside the root `problems_dir` (posix, sorted)."""
    return sorted(
        path.relative_to(problems_dir).as_posix() for path in iter_problem_dirs(problems_dir)
    )


def problems_under(problems_dir: Path, group_path: str) -> list[str]:
    """The paths of problems (inside the root) in the group `group_path` — a directory inside the root
    (`""` — the root itself). Empty if there are no problems under it or it's a problem itself."""
    problems_dir = Path(problems_dir)
    if group_path and is_problem_dir(problems_dir / group_path):
        return []
    return sorted(
        path.relative_to(problems_dir).as_posix()
        for path in iter_problem_dirs(problems_dir / group_path)
    )


def check_new_problem_location(problems_dir: Path, problem_path: str) -> None:
    """Checks that a new problem can be set up in `problems_dir/<problem_path>`:
    this directory doesn't lie inside another problem, there are no problems
    inside it, and its `problem_id` isn't taken by a problem elsewhere — neither in this
    root, nor in the neighboring one (`problems/` and `examples/` lie side by side). Otherwise
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
