"""`orchestrator contests/<contest path> polygon status`: the contest's problems on Polygon
against its `contest.yaml` (`orchestrator/contests.py`).

The Polygon API has only one contest method, `contest.problems` (reading the problem
list), so the orchestrator can't add a problem to a contest or reorder it — the status
says what to do on Polygon by hand. Problems are matched by the Polygon problem id from
`polygon_state.json`; a problem without one is matched by its `problem_id` as the name
(then it is on Polygon, but not linked locally — `polygon pull` links it).

For each problem also: whether its working copy on Polygon has uncommitted changes
(`modified` of the contest listing) and how many polygon steps are not in sync locally
(`compute_polygon_step_statuses`, as `polygon status` of the problem).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.contests import Contest
from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.pipeline import compute_polygon_step_statuses
from orchestrator.polygon.state import load_polygon_state
from orchestrator.polygon.steps.base import STATUS_DONE
from orchestrator.problem_paths import problem_address, problem_id_for


@dataclass(frozen=True)
class PolygonContestProblem:
    """A problem of the contest on Polygon."""

    letter: str | None  # None — the response didn't give the letters
    polygon_id: int
    name: str
    modified: bool = False
    deleted: bool = False


@dataclass
class ContestRow:
    """One line of the status: a problem of the contest on Polygon, of `contest.yaml`,
    or of both."""

    letter: str | None
    name: str  # the problem_id locally, else the name on Polygon
    address: str | None  # None — not in contest.yaml
    polygon_id: int | None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.notes


def fetch_contest_problems(client: PolygonClient, contest: Contest) -> list[PolygonContestProblem]:
    """`contest.problems`. The documentation says "a list of Problem objects"; the letters
    are taken if the result is a mapping letter -> Problem (NOTE: which of the two forms
    Polygon returns isn't verified on the real API)."""
    params: dict[str, str | bytes] = {"contestId": str(contest.polygon_id)}
    if contest.pin is not None:
        params["pin"] = contest.pin
    result = client.call("contest.problems", params)
    if isinstance(result, dict):
        items: list[tuple[str | None, Any]] = sorted(result.items())
    else:
        items = [(None, problem) for problem in result or []]
    return [
        PolygonContestProblem(
            letter=letter,
            polygon_id=int(problem["id"]),
            name=str(problem.get("name", "")),
            modified=bool(problem.get("modified", False)),
            deleted=bool(problem.get("deleted", False)),
        )
        for letter, problem in items
    ]


def contest_status(
    contest: Contest, polygon_problems: list[PolygonContestProblem]
) -> list[ContestRow]:
    """The rows: the contest's problems on Polygon in their order, then the problems of
    `contest.yaml` that aren't in it."""
    by_id = {problem.polygon_id: problem for problem in polygon_problems}
    by_name: dict[str, list[PolygonContestProblem]] = {}
    for problem in polygon_problems:
        by_name.setdefault(problem.name, []).append(problem)

    rows_by_id: dict[int, ContestRow] = {}
    outside: list[ContestRow] = []
    for ref in contest.problems:
        problem_id = problem_id_for(ref.problems_dir, ref.problem_path)
        address = problem_address(ref.problems_dir, ref.problem_path)
        state = load_polygon_state(ref.problem_path, problems_dir=ref.problems_dir)
        if state is None:
            namesakes = by_name.get(problem_id, [])
            if len(namesakes) == 1 and namesakes[0].polygon_id not in rows_by_id:
                found = namesakes[0]
                rows_by_id[found.polygon_id] = ContestRow(
                    found.letter,
                    problem_id,
                    address,
                    found.polygon_id,
                    ["not linked locally: run `polygon pull` for the problem"],
                )
            else:
                outside.append(
                    ContestRow(
                        None,
                        problem_id,
                        address,
                        None,
                        [
                            "not on Polygon: run `polygon push` for the problem, then add it "
                            "to the contest on Polygon"
                        ],
                    )
                )
            continue

        notes = _local_notes(ref.problem_path, ref.problems_dir)
        found = by_id.get(state.polygon_id)
        if found is None:
            outside.append(
                ContestRow(
                    None,
                    problem_id,
                    address,
                    state.polygon_id,
                    [f"not in the contest: add problem {state.polygon_id} to it on Polygon"]
                    + notes,
                )
            )
            continue
        if state.polygon_id in rows_by_id:
            other = rows_by_id[state.polygon_id].address
            notes = [f"the same Polygon problem as {other}"] + notes
        rows_by_id[state.polygon_id] = ContestRow(
            found.letter, problem_id, address, state.polygon_id, _polygon_notes(found) + notes
        )

    rows = []
    for problem in polygon_problems:
        row = rows_by_id.get(problem.polygon_id)
        if row is None:
            row = ContestRow(
                problem.letter,
                problem.name,
                None,
                problem.polygon_id,
                ["in the contest, but not in contest.yaml"] + _polygon_notes(problem),
            )
        rows.append(row)
    return rows + outside


def _polygon_notes(problem: PolygonContestProblem) -> list[str]:
    notes = []
    if problem.deleted:
        notes.append("deleted on Polygon")
    if problem.modified:
        notes.append("uncommitted changes in the Polygon working copy")
    return notes


def _local_notes(problem_path: str, problems_dir: Path) -> list[str]:
    pending = [
        outcome.step_name
        for outcome in compute_polygon_step_statuses(problem_path, problems_dir=problems_dir)
        if outcome.status != STATUS_DONE
    ]
    if not pending:
        return []
    return [f"{len(pending)} polygon step(s) not in sync: see `polygon status` of the problem"]
