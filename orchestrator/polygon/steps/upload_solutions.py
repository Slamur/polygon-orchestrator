"""A Polygon step — uploading all files from `problems/<problem_id>/solutions/`
(the result of the `solutions_draft` generative step) to Polygon
(`problem.saveSolution`) with a verdict tag for each.

A solution that already exists on Polygon is uploaded with its current tag:
the author sets the tags there, and push doesn't change them. A new solution's tag is derived
from the file name by the `<verdict>_<language>_<description>_<author>.<ext>` scheme
(`prompts/solutions_draft/system.md`, rule 5):

- `MA` (Main correct — the reference for generating the tests' answers,
  `templates/tutorials/solutions.md`, "Solutions in Polygon") is assigned only
  if Polygon doesn't have one yet (Polygon rejects a second `MA`): then one `ok` file
  gets it, chosen by the language priority
  `_MAIN_LANGUAGE_PRIORITY`, within a language — alphabetically by name; this is the only
  case when the tag of a solution that already exists on Polygon changes;
- the other `ok` -> `OK`, `tl`/`tle` -> `TL`, `tl_ok` -> `TO` ("TL or OK"),
  `wa` -> `WA`, `ml`/`mle` -> `ML`;
- everything else allowed (`re`, `il` — Idleness limit exceeded in an
  interactive problem) -> `RJ` ("Incorrect").

Risk 1: `Solutions.languages` in the spec is a `list[str]` without restricting
the values, and the model may write the language in the file name differently (`py`,
`python`, `python3`, `c++`). Known variants are mapped to one name
via `_LANGUAGE_ALIASES`; if the language is still not from the priority, the step doesn't
fail, but writes a WARNING and takes as Main correct the alphabetically first
`ok` file — the author should check the choice in the UI.

Risk 2: `sourceType` isn't passed — it isn't confirmed which value
Polygon expects for each language; it's assumed it determines the language by the
extension, as in `upload_validator`/`upload_generators`. The first real
call must be checked in the UI.

All file names are parsed and the presence of an `ok` file is checked before the first
network call: a file with an unsuitable name is a `PolygonStepError`, not a quiet
skip. The current tags are read from `problem.solutions` — with one request and
only if there's something to upload.

Only solutions whose contents differ from the sync
base are uploaded; the tag isn't part of the base. Changing the tags of solutions that already exist on
Polygon — including moving Main correct — has to be done in the UI, `polygon push
--force` doesn't change them either. If you remove `MA` there, the next upload of a solution
assigns it again.

If a solution was deleted or renamed since the last upload, the old file on
Polygon stays — the step doesn't delete it.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import digest, solution_key
from orchestrator.problem_paths import is_removed_mark, problem_address
from orchestrator.spec import split_solution_stem

logger = logging.getLogger(__name__)

STEP_NAME = "upload_solutions"

# The Main correct choice priority — exactly in this order. The values are languages
# after normalization via `_LANGUAGE_ALIASES`.
_MAIN_LANGUAGE_PRIORITY = ["cpp", "java", "py"]

# Spellings of a language in a file name -> the name from `_MAIN_LANGUAGE_PRIORITY`.
_LANGUAGE_ALIASES = {
    "python": "py",
    "python3": "py",
    "py3": "py",
    "c++": "cpp",
}

# Matches `orchestrator.spec._ALLOWED_VERDICTS` (the allowed
# `solutions.wanted_verdicts`).
_VALID_VERDICTS = {"ok", "wa", "tl", "tl_ok", "tle", "ml", "mle", "re", "il"}


@dataclass(frozen=True)
class _ParsedSolution:
    path: Path
    verdict: str
    language: str


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads each solution that differs from the sync base, with the tag
    from `_tag_to_send` (`ctx.spec` isn't used).

    Both dependencies (`create_problem`, `solutions_draft`), parsing the names and
    the presence of an `ok` file — over the whole set and before the first network call.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    solutions = _read_solutions(ctx.problem_id, ctx.problems_dir)

    parsed = [_parse_solution_filename(Path(name)) for name, _ in solutions]
    _require_ok_solution(parsed)

    changed = changed_items(ctx, _items(solutions))
    if not changed:
        return StepResult(NO_CHANGES_MESSAGE)

    polygon_tags = _tags_on_polygon(client, polygon_id)
    main_name = None if "MA" in polygon_tags.values() else _choose_main(parsed)
    tags = {item.path.name: _tag_to_send(item, main_name, polygon_tags) for item in parsed}

    sent = []
    for name, content in solutions:
        if solution_key(name) not in changed:
            continue
        client.call(
            "problem.saveSolution",
            {
                "problemId": str(polygon_id),
                "name": name,
                "tag": tags[name],
                "file": content,
            },
        )
        sent.append(f"{name}={tags[name]}")

    return StepResult(
        f"uploaded {len(sent)} of {len(solutions)} solution(s) to Polygon id={polygon_id}: "
        f"{', '.join(sent)}",
        changed,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`): a new or changed solution
    makes the step "stale"."""
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=solution_key(""),
        read_items=lambda: _items(_read_solutions(problem_id, problems_dir)),
    )


def _items(solutions: list[tuple[str, bytes]]) -> dict[str, str]:
    """Only the contents, without the tag — see the module docstring."""
    return {solution_key(name): digest(content) for name, content in solutions}


def _read_solutions(problem_id: str, problems_dir: Path) -> list[tuple[str, bytes]]:
    """[(name, contents), ...] for all files of `solutions/` alphabetically by
    name, except those marked by a step as removed (`is_removed_mark`). No
    directory or it's empty — `PolygonStepError`."""
    solutions_dir = Path(problems_dir) / problem_id / "solutions"
    if not solutions_dir.is_dir():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': directory {solutions_dir} not found — first "
            f"run the generative step solutions_draft (`orchestrator {address} llm generate "
            f"--step solutions_draft`)"
        )
    paths = sorted(
        (p for p in solutions_dir.iterdir() if p.is_file() and not is_removed_mark(p)),
        key=lambda p: p.name,
    )
    if not paths:
        raise PolygonStepError(f"'{problem_id}': no files in {solutions_dir}")
    return [(path.name, path.read_bytes()) for path in paths]


def _parse_solution_filename(path: Path) -> _ParsedSolution:
    """Parses `<verdict>_<language>_<description>_<author>.<ext>`.
    `description` may contain underscores — only the first two
    tokens are taken as verdict/language, the rest isn't parsed (author isn't needed
    for the tag); a compound verdict (`tl_ok`) is one token
    (`split_solution_stem`). At least 3 tokens in the stem."""
    parts = split_solution_stem(path.stem)
    if len(parts) < 3:
        raise PolygonStepError(
            f"'{path.name}': file name does not match the pattern "
            "<verdict>_<language>_<description>_<author>.<ext> "
            f"(fewer than 3 '_'-separated tokens: {parts})"
        )
    verdict = parts[0].lower()
    language = _LANGUAGE_ALIASES.get(parts[1].lower(), parts[1].lower())
    if verdict not in _VALID_VERDICTS:
        raise PolygonStepError(
            f"'{path.name}': verdict '{verdict}' is not in the allowed set "
            f"{sorted(_VALID_VERDICTS)} — check the file name manually"
        )
    return _ParsedSolution(path=path, verdict=verdict, language=language)


def _require_ok_solution(parsed: list[_ParsedSolution]) -> None:
    if not any(p.verdict == "ok" for p in parsed):
        raise PolygonStepError(
            "no solution with verdict 'ok' — nothing to pick as Main "
            "correct (expected at least one ok_* file)"
        )


def _tags_on_polygon(client: PolygonClient, polygon_id: int) -> dict[str, str]:
    """A solution's name on Polygon -> its current tag."""
    solutions = client.call("problem.solutions", {"problemId": str(polygon_id)}) or []
    return {solution["name"]: solution["tag"] for solution in solutions}


def _choose_main(parsed: list[_ParsedSolution]) -> str:
    """The Main correct file name — the alphabetically first `ok` file of the first language from
    `_MAIN_LANGUAGE_PRIORITY` for which such a file exists. If no
    language from the priority occurred — a WARNING and the alphabetically first
    `ok` file (risk 1 in the module docstring), not an error."""
    _require_ok_solution(parsed)
    ok_names = sorted(p.path.name for p in parsed if p.verdict == "ok")

    for lang in _MAIN_LANGUAGE_PRIORITY:
        candidates = sorted(p.path.name for p in parsed if p.verdict == "ok" and p.language == lang)
        if candidates:
            return candidates[0]

    logger.warning(
        "none of the priority languages %s found among ok solutions %s — "
        "the language in file names may be spelled differently than expected; "
        "Main correct is the alphabetically first one, check the choice in the Polygon UI",
        _MAIN_LANGUAGE_PRIORITY,
        ok_names,
    )
    return ok_names[0]


def _tag_to_send(
    parsed: _ParsedSolution, main_name: str | None, polygon_tags: dict[str, str]
) -> str:
    """The solution's current tag on Polygon, except for the Main correct chosen now;
    `main_name` — `None` if Polygon already has a Main correct."""
    name = parsed.path.name
    if name != main_name and name in polygon_tags:
        return polygon_tags[name]
    return _tag_for(parsed, main_name)


def _tag_for(parsed: _ParsedSolution, main_name: str | None) -> str:
    if parsed.path.name == main_name:
        return "MA"
    if parsed.verdict == "ok":
        return "OK"
    if parsed.verdict in ("tl", "tle"):
        return "TL"
    if parsed.verdict == "tl_ok":
        return "TO"
    if parsed.verdict == "wa":
        return "WA"
    if parsed.verdict in ("ml", "mle"):
        return "ML"
    return "RJ"  # re, il — "Incorrect"


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
