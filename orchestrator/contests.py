"""Contests: a Polygon contest and the local problems it is made of.

A contest lies in `contests/` at any depth (`contests/2026/round-1`), like the problems in
`problems/`: a contest directory is the first directory on the way down that contains
`contest.yaml`; contests aren't nested in each other. `contests/` is in `.gitignore`
except `.gitkeep`: a contest's name, its Polygon id and its problem list are as private
as the problems themselves. The address of a contest in the CLI is its directory with
the root: `orchestrator contests/2026/round-1 polygon status`.

`contest.yaml`:

    polygon_id: 12345          # the contest's id on Polygon (contestId of the API)
    uid: 50431a121273b7e31f4200e7  # optional: the contest's UID (its links on Polygon,
                                   # c/<uid>/contest.xml)
    pin: "0000"                # optional: the contest's pin code, if it has one
    problems:                  # problem or group addresses, as in the CLI
      - problems/2026/round-1/my-task
      - examples               # a group: every problem under it

A problem may be in several contests: the contest only lists it, the problem doesn't know
about its contests.

`resources/` of a contest — the contest's own versions of its statement files
(`CONTEST_RESOURCE_FILENAMES`: `olymp.sty`, `statements.ftl` the whole contest's statement
PDF is built with); without one the default from `resources/polygon-statement/` of the
repository is meant. The Polygon API has no methods for a contest's files, so they are
kept here and synced with Polygon by hand.

No heavy imports: `cli.py` lists the contests on every Tab (yaml is imported when a
contest is loaded).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.problem_paths import (
    _COMPONENT_PATTERN,
    ProblemPathError,
    parse_address_path,
    parse_problem_address,
    problems_under,
)
from orchestrator.resources import STATEMENT_RESOURCES_DIR

CONTESTS_DIR = Path("contests")
CONTEST_FILENAME = "contest.yaml"
CONTEST_RESOURCES_DIRNAME = "resources"
# The statement files of a contest on Polygon: the template of the whole document and the
# macros it uses (`problem.tex` belongs to each problem, not to the contest).
CONTEST_RESOURCE_FILENAMES = ("olymp.sty", "statements.ftl")
# The contest's UID on Polygon: hexadecimal, as in its links (`c/<uid>/contest.xml`).
_UID_PATTERN = re.compile(r"^[0-9a-f]+$")


class ContestError(ValueError):
    """An invalid contest address or `contest.yaml`."""


@dataclass(frozen=True)
class ContestProblemRef:
    """A problem of a contest: its root (`problems/` or `examples/` under the base
    directory) and its path inside the root."""

    problems_dir: Path
    problem_path: str


@dataclass
class Contest:
    """The contents of `contests/<contest path>/contest.yaml`."""

    contest_path: str
    contest_dir: Path
    polygon_id: int
    pin: str | None = None
    uid: str | None = None
    problems: list[ContestProblemRef] = field(default_factory=list)


def is_contest_address(address: str) -> bool:
    """The CLI address names a contest (starts with `contests/`)."""
    parts = [part for part in address.strip().split("/") if part]
    return bool(parts) and parts[0] == CONTESTS_DIR.name


def parse_contest_address(address: str) -> str:
    """The contest's path inside `contests/` by its CLI address."""
    parts = [part for part in address.strip().split("/") if part]
    if not parts or parts[0] != CONTESTS_DIR.name or len(parts) == 1:
        raise ContestError(
            f"contest address '{address}' must be '{CONTESTS_DIR.name}/' followed by the "
            "contest directory, e.g. contests/2026/round-1"
        )
    for part in parts[1:]:
        if not _COMPONENT_PATTERN.match(part):
            raise ContestError(
                f"contest address '{address}': directory name '{part}' must be ASCII "
                "letters/digits/'-'/'_', not starting or ending with '-'"
            )
    return "/".join(parts[1:])


def contest_address(contest_path: str) -> str:
    return f"{CONTESTS_DIR.name}/{contest_path}"


def is_contest_dir(path: Path) -> bool:
    return (Path(path) / CONTEST_FILENAME).is_file()


def iter_contest_dirs(directory: Path) -> Iterator[Path]:
    """Contest directories under `directory`; hidden directories are skipped, the traversal
    doesn't descend into a contest directory."""
    directory = Path(directory)
    for current, dirnames, _ in os.walk(directory):
        current = Path(current)
        if current != directory and is_contest_dir(current):
            dirnames.clear()
            yield current
        else:
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))


def contest_paths(contests_dir: Path) -> list[str]:
    """The paths of the contests inside `contests_dir` (posix, sorted)."""
    return sorted(
        path.relative_to(contests_dir).as_posix() for path in iter_contest_dirs(contests_dir)
    )


def load_contest(base_dir: Path, contest_path: str) -> Contest:
    """Reads and checks `contest.yaml` of the contest `contest_path`; the problem list is
    expanded: a group address becomes the problems under it, repeats are dropped, the order
    is kept. Any problem in the file — a `ContestError`."""
    import yaml

    base_dir = Path(base_dir)
    contest_dir = base_dir / CONTESTS_DIR / contest_path
    config_path = contest_dir / CONTEST_FILENAME
    if not config_path.is_file():
        raise ContestError(f"no contest {contest_address(contest_path)}: {config_path} not found")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContestError(f"{config_path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ContestError(f"{config_path}: expected a mapping with polygon_id and problems")
    unknown = sorted(set(data) - {"polygon_id", "uid", "pin", "problems"})
    if unknown:
        raise ContestError(f"{config_path}: unknown field(s): {', '.join(unknown)}")

    polygon_id = data.get("polygon_id")
    if isinstance(polygon_id, bool) or not isinstance(polygon_id, int) or polygon_id <= 0:
        raise ContestError(f"{config_path}: polygon_id must be the contest's numeric Polygon id")
    pin = data.get("pin")
    if pin is not None:
        if isinstance(pin, bool) or not isinstance(pin, (str, int)):
            raise ContestError(f"{config_path}: pin must be a string")
        pin = str(pin)

    uid = data.get("uid")
    if uid is not None and not (isinstance(uid, str) and _UID_PATTERN.match(uid)):
        raise ContestError(f"{config_path}: uid must be the contest's hexadecimal Polygon UID")

    addresses = data.get("problems") or []
    if not isinstance(addresses, list) or not all(isinstance(a, str) for a in addresses):
        raise ContestError(f"{config_path}: problems must be a list of problem addresses")
    return Contest(
        contest_path=contest_path,
        contest_dir=contest_dir,
        polygon_id=polygon_id,
        pin=pin,
        uid=uid,
        problems=_expand_problems(base_dir, config_path, addresses),
    )


def _expand_problems(
    base_dir: Path, config_path: Path, addresses: list[str]
) -> list[ContestProblemRef]:
    problems: list[ContestProblemRef] = []
    for address in addresses:
        try:
            root, path = parse_address_path(address)
            group = problems_under(base_dir / root, path)
            if not group:
                root, path = parse_problem_address(address)
                if not (base_dir / root / path).is_dir():
                    raise ProblemPathError(f"no problem directory {base_dir / root / path}")
                group = [path]
        except ProblemPathError as exc:
            raise ContestError(f"{config_path}: {exc}") from None
        for problem_path in group:
            ref = ContestProblemRef(base_dir / root, problem_path)
            if ref not in problems:
                problems.append(ref)
    return problems


@dataclass(frozen=True)
class ContestResource:
    name: str
    path: Path | None  # the file meant: the contest's own, else the default; None — neither
    own: bool


def contest_resources(contest_dir: Path) -> list[ContestResource]:
    """The contest's statement files (`CONTEST_RESOURCE_FILENAMES`) and any other file in
    its `resources/`: which version is meant — the contest's own or the default."""
    resources_dir = Path(contest_dir) / CONTEST_RESOURCES_DIRNAME
    own_names = (
        sorted(path.name for path in resources_dir.iterdir() if path.is_file())
        if resources_dir.is_dir()
        else []
    )
    names = list(CONTEST_RESOURCE_FILENAMES) + [
        name for name in own_names if name not in CONTEST_RESOURCE_FILENAMES
    ]
    result = []
    for name in names:
        if name in own_names:
            result.append(ContestResource(name, resources_dir / name, own=True))
        else:
            default = STATEMENT_RESOURCES_DIR / name
            result.append(ContestResource(name, default if default.is_file() else None, False))
    return result
