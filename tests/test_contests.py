"""Contests (`orchestrator/contests.py`) and their status against Polygon
(`orchestrator/polygon/contest.py`, `orchestrator contests/<path> polygon status`)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import cli
from orchestrator.contests import (
    ContestError,
    ContestProblemRef,
    contest_resources,
    is_contest_address,
    load_contest,
    parse_contest_address,
)
from orchestrator.polygon import contest as polygon_contest
from orchestrator.polygon.contest import (
    PolygonContestProblem,
    contest_status,
    fetch_contest_problems,
)
from orchestrator.resources import STATEMENT_RESOURCES_DIR


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _problem(base: Path, address: str, polygon_id: int | None = None) -> None:
    problem_dir = base / address
    _write(problem_dir / "authoring" / "spec.yaml", "problem_id: x\n")
    if polygon_id is not None:
        state = {
            "problem_id": problem_dir.name,
            "polygon_id": polygon_id,
            "created_at": "2026-01-01T00:00:00Z",
        }
        _write(problem_dir / "polygon_state.json", json.dumps(state))


def _contest(base: Path, text: str, path: str = "2026/round-1") -> Path:
    return _write(base / "contests" / path / "contest.yaml", text)


@pytest.fixture
def base(tmp_path):
    _problem(tmp_path, "problems/round/task-a", polygon_id=11)
    _problem(tmp_path, "problems/round/task-b", polygon_id=12)
    _problem(tmp_path, "examples/array/prefix-sums")
    return tmp_path


def test_contest_address():
    assert is_contest_address("contests/2026/round-1/")
    assert not is_contest_address("problems/contests")
    assert parse_contest_address("contests/2026/round-1/") == "2026/round-1"
    with pytest.raises(ContestError):
        parse_contest_address("contests")
    with pytest.raises(ContestError):
        parse_contest_address("contests/-bad")


def test_load_contest_expands_groups_and_drops_repeats(base):
    _contest(
        base,
        "polygon_id: 500\nuid: 50431a12\npin: 1234\nproblems:\n"
        "  - problems/round/task-b\n  - problems/round\n  - examples\n",
    )

    contest = load_contest(base, "2026/round-1")

    assert contest.polygon_id == 500
    assert contest.pin == "1234"
    assert contest.uid == "50431a12"
    assert contest.problems == [
        ContestProblemRef(base / "problems", "round/task-b"),
        ContestProblemRef(base / "problems", "round/task-a"),
        ContestProblemRef(base / "examples", "array/prefix-sums"),
    ]


@pytest.mark.parametrize(
    "text, message",
    [
        ("problems: []\n", "polygon_id"),
        ("polygon_id: 1\nproblems: [problems/missing]\n", "no problem directory"),
        ("polygon_id: 1\nproblems: [elsewhere/x]\n", "must start with"),
        ("polygon_id: 1\nproblem: []\n", "unknown field"),
        ("polygon_id: 1\nuid: 123\nproblems: []\n", "uid"),
        ("polygon_id: 1\nuid: not-hex\nproblems: []\n", "uid"),
        ("polygon_id: 1\nproblems: problems/round\n", "list of problem addresses"),
    ],
)
def test_load_contest_rejects_invalid_config(base, text, message):
    _contest(base, text)

    with pytest.raises(ContestError, match=message):
        load_contest(base, "2026/round-1")


def test_load_contest_without_config(base):
    with pytest.raises(ContestError, match="not found"):
        load_contest(base, "nothing")


def test_contest_resources_take_the_own_version_else_the_default(tmp_path):
    contest_dir = tmp_path / "contests" / "c"
    _write(contest_dir / "resources" / "olymp.sty", "% own\n")
    _write(contest_dir / "resources" / "logo.png", "png")

    resources = {resource.name: resource for resource in contest_resources(contest_dir)}

    assert list(resources) == ["olymp.sty", "statements.ftl", "logo.png"]
    assert resources["olymp.sty"].own
    assert resources["olymp.sty"].path == contest_dir / "resources" / "olymp.sty"
    assert not resources["statements.ftl"].own
    assert resources["statements.ftl"].path == STATEMENT_RESOURCES_DIR / "statements.ftl"
    assert resources["logo.png"].own


class _FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return self.result


def test_fetch_contest_problems_takes_letters_from_a_mapping(base):
    _contest(base, "polygon_id: 500\npin: '0007'\nproblems: []\n")
    contest = load_contest(base, "2026/round-1")
    client = _FakeClient(
        {"B": {"id": 12, "name": "task-b"}, "A": {"id": 11, "name": "task-a", "modified": True}}
    )

    problems = fetch_contest_problems(client, contest)

    assert client.calls == [("contest.problems", {"contestId": "500", "pin": "0007"})]
    assert problems == [
        PolygonContestProblem("A", 11, "task-a", modified=True),
        PolygonContestProblem("B", 12, "task-b"),
    ]


def test_fetch_contest_problems_accepts_a_list(base):
    _contest(base, "polygon_id: 500\nproblems: []\n")
    contest = load_contest(base, "2026/round-1")

    problems = fetch_contest_problems(_FakeClient([{"id": 11, "name": "task-a"}]), contest)

    assert problems == [PolygonContestProblem(None, 11, "task-a")]


@pytest.fixture
def all_synced():
    with patch.object(polygon_contest, "_local_notes", return_value=[]):
        yield


def test_contest_status_matches_problems_by_polygon_id_and_by_name(base, all_synced):
    _problem(base, "problems/round/task-c", polygon_id=13)
    _contest(
        base,
        "polygon_id: 500\nproblems:\n  - problems/round\n  - examples\n",
    )
    contest = load_contest(base, "2026/round-1")
    on_polygon = [
        PolygonContestProblem("A", 12, "task-b", modified=True),
        PolygonContestProblem("B", 11, "task-a"),
        PolygonContestProblem("C", 30, "array--prefix-sums"),
        PolygonContestProblem("D", 40, "foreign"),
    ]

    rows = contest_status(contest, on_polygon)

    assert [(row.letter, row.name, row.address) for row in rows] == [
        ("A", "task-b", "problems/round/task-b"),
        ("B", "task-a", "problems/round/task-a"),
        ("C", "array--prefix-sums", "examples/array/prefix-sums"),
        ("D", "foreign", None),
        (None, "task-c", "problems/round/task-c"),
    ]
    notes = [row.notes for row in rows]
    assert notes[0] == ["uncommitted changes in the Polygon working copy"]
    assert notes[1] == []
    assert "polygon pull" in notes[2][0]
    assert "not in contest.yaml" in notes[3][0]
    assert "add problem 13" in notes[4][0]


def test_contest_status_problem_not_on_polygon(base, all_synced):
    _contest(base, "polygon_id: 500\nproblems: [examples]\n")

    (row,) = contest_status(load_contest(base, "2026/round-1"), [])

    assert row.letter is None and row.polygon_id is None
    assert "not on Polygon" in row.notes[0]


def test_contest_status_reports_steps_not_pushed(base):
    _contest(base, "polygon_id: 500\nproblems: [problems/round/task-a]\n")

    (row,) = contest_status(
        load_contest(base, "2026/round-1"), [PolygonContestProblem("A", 11, "task-a")]
    )

    assert row.notes and "polygon step(s) not in sync" in row.notes[0]


def _run(base: Path, *command: str) -> int:
    return cli.main(["--base-dir", str(base), *command])


def test_cli_contest_status(base, all_synced, capsys):
    _contest(base, "polygon_id: 500\nuid: abc123\nproblems: [problems/round]\n")
    client = _FakeClient({"A": {"id": 11, "name": "task-a"}, "B": {"id": 12, "name": "task-b"}})

    with patch("orchestrator.polygon.client.PolygonClient", return_value=client):
        rc = _run(base, "contests/2026/round-1", "polygon", "status")

    out = capsys.readouterr().out
    assert rc == 0
    assert "Polygon contest 500 (uid abc123), 2 problem(s) on Polygon, 2 in contest.yaml" in out
    assert "A  task-a  problems/round/task-a: ok" in out
    assert "statement files:" in out
    assert "olymp.sty: default" in out


def test_cli_contest_status_fails_on_a_row_needing_attention(base, all_synced, capsys):
    _contest(base, "polygon_id: 500\nproblems: [problems/round]\n")

    with patch("orchestrator.polygon.client.PolygonClient", return_value=_FakeClient({})):
        rc = _run(base, "contests/2026/round-1", "polygon", "status")

    assert rc == 1
    assert "add problem 11" in capsys.readouterr().out


def test_cli_contest_rejects_other_commands(base, capsys):
    _contest(base, "polygon_id: 500\nproblems: []\n")

    with pytest.raises(SystemExit):
        _run(base, "contests/2026/round-1", "polygon", "push")

    assert "only 'polygon status'" in capsys.readouterr().err


def test_cli_contest_with_invalid_config(base, capsys):
    _contest(base, "problems: []\n")

    assert _run(base, "contests/2026/round-1", "polygon", "status") == 1
    assert "polygon_id" in capsys.readouterr().err
