"""Polygon step 6 — uploading the statement from `problems/<problem_id>/statement/<language
code>/` (in the main language — the result of the `statement_draft` generative step):
legend/input/output/notes via `problem.saveStatement` and the example tests
from `statement/examples/` as the first tests
(`problem.saveTest`, `testIndex` 1..N, `testUseInStatements=true`).

Only the examples' input goes to Polygon — the output from `sample_examples` isn't
read or used in this step (docs/SPEC_FORMAT.md: the output "isn't translated
any further"). Polygon computes the answer for these tests itself
from the main solution once it's uploaded by a separate (not yet
written) step — this is a deliberate order, not a mistake.

The examples' `testIndex` is always 1..N without a shift: templates/tutorials/statement.md
requires the example tests to be the first tests.

The parameters of `problem.saveTest` — by docs/POLYGON_API.md. The "use
in statements" flag is called `testUseInStatements` (`useInStatements` is the name of the field in
the returned Test object, not of a parameter): Polygon silently ignores an unknown parameter,
and the test is uploaded without this flag.

Test groups (`generation.script_style: "groups"`) are NOT supported
by this step: the examples are uploaded without `testGroup`, i.e. into Polygon's default
group. For a problem with groups this may not be what's needed —
a deliberate MVP limitation, the examples' group will have to be set by hand.

If the number of examples decreased since the last upload, the extra tests with
indices > N stay on Polygon — the step doesn't delete them.

An interactive problem (the spec has an `interactor` section): the interaction
protocol `statement/<code>/interaction.tex` goes to `problem.saveStatement`
as the `interaction` parameter (no file — an error).

The tutorial `statement/<code>/tutorial.tex` (optional, written by the author, `polygon pull`
or the `tutorial_draft` step) goes there too as the `tutorial` parameter. No file —
the parameter isn't passed (Polygon leaves the tutorial as is), but if the sync
base had a tutorial and the file was deleted, the tutorial is cleared with an empty string.

An example's input/output for statements (`example_<N>.input_for_statement.txt`,
`example_<N>.output_for_statement.txt` — each on its own, for a normal
problem the author sets them, for an interactive one it's the dialogue of the solution with the interactor)
go to `problem.saveTest` as the parameters
`testInputForStatements`/`testOutputForStatements`, the flag
`example_<N>.verify_for_statement.txt` (the file exists and contains `true`) —
`verifyInputOutputForStatements=true`. No file — the parameter isn't
passed (Polygon leaves the field as is), but if the sync base
had the field and the file was deleted, the field is cleared: an empty string, for the flag —
`false`.

The problem's name is taken from `statement/<code>/name.txt`, not from the spec:
the spec's name in this language (`title.ru` for `ru`) is only the initial
value with which the step fills in `name.txt` if it doesn't exist yet (no
name in the spec either — an error). Otherwise a name changed on Polygon and
downloaded by `polygon pull` (it writes it into the same `name.txt`) would be overwritten by the next
push with the value from the spec.

Languages (`orchestrator/statement_languages.py`): each directory
`statement/<code>/` goes with its own `problem.saveStatement` with `lang` — the
language's name in Polygon (`ru` -> `russian`). The directory of the main language (the spec's
`language`) is mandatory, the others are written by the author or `polygon pull`. The examples are shared
by all languages. A deleted language directory doesn't delete the statement on Polygon —
the author does that.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import load_polygon_state
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
from orchestrator.polygon.sync import (
    dialogue_keys,
    digest,
    example_key,
    statement_file_key,
    statement_name_key,
    verify_key,
)
from orchestrator.problem_paths import TUTORIAL_FILENAME, ProblemPathError
from orchestrator.spec import Title
from orchestrator.statement_languages import (
    EXAMPLES_DIRNAME,
    STATEMENT_DIRNAME,
    polygon_language,
    statement_languages,
)
from orchestrator.steps.statement_draft import (
    INTERACTION_FILE,
    dialogue_filenames,
    verify_filename,
)

STEP_NAME = "upload_statement"

_TESTSET = "tests"

_TEX_PARAM_NAMES = {
    "legend.tex": "legend",
    "input_format.tex": "input",
    "output_format.tex": "output",
    "notes.tex": "notes",
}

_EXAMPLE_PATTERN = re.compile(r"^example_(\d+)\.txt$")

NAME_FILENAME = "name.txt"
INTERACTION_FILENAME = INTERACTION_FILE
INTERACTION_PARAM = "interaction"
TUTORIAL_PARAM = "tutorial"

_VERIFY_VALUE = b"true"


@dataclass(frozen=True)
class Statement:
    """The statement in one language: `code` — the directory `statement/<code>/`,
    `tex_files` — file name -> contents (`_read_statement_dir`), `name` —
    from `name.txt` (`None` — there's no file)."""

    code: str
    tex_files: dict[str, bytes]
    name: str | None

    @property
    def language(self) -> str:
        """The language's name in Polygon."""
        return polygon_language(self.code)

    def items(self) -> dict[str, str]:
        """The sync base elements: the tex files and the name (if known)."""
        items = {
            statement_file_key(filename, self.code): digest(content)
            for filename, content in self.tex_files.items()
        }
        if self.name is not None:
            items[statement_name_key(self.code)] = digest(self.name)
        return items

    @property
    def tutorial_key(self) -> str:
        return statement_file_key(TUTORIAL_FILENAME, self.code)


@dataclass(frozen=True)
class Example:
    """A statement example: `testIndex`, the test's input and the optional fields of the test
    for the statement — sync base key -> (`problem.saveTest` parameter,
    value); only fields whose files exist on disk have it."""

    index: int
    input: bytes
    fields: dict[str, tuple[str, str]]

    def items(self) -> dict[str, str]:
        return {
            example_key(self.index): digest(self.input),
            **{key: digest(value.encode("utf-8")) for key, (_, value) in self.fields.items()},
        }


def _optional_fields(index: int) -> list[tuple[str, str, str, str]]:
    """The optional fields of an example test: (file name, base key, the
    `problem.saveTest` parameter, the value on clearing)."""
    input_name, output_name = dialogue_filenames(index)
    input_key, output_key = dialogue_keys(index)
    return [
        (input_name, input_key, "testInputForStatements", ""),
        (output_name, output_key, "testOutputForStatements", ""),
        (verify_filename(index), verify_key(index), "verifyInputOutputForStatements", "false"),
    ]


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads the statement of each language (if any of its tex files or the
    name differ from the sync base — `problem.saveStatement`
    accepts them only together), then the changed examples in the order of
    `testIndex`.

    Both dependencies (`create_problem`, `statement_draft`) are checked before the
    first network call.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    statement_dir = _statement_dir(ctx.problem_id, ctx.problems_dir)
    statements = read_statements(
        statement_dir,
        language=ctx.spec.language,
        interactive=ctx.spec.interactive,
        title=ctx.spec.title,
    )
    examples = _read_examples(statement_dir)

    example_items = _example_items(examples)
    uploaded: dict[str, str | None] = {}
    done: list[str] = []

    for statement in statements:
        statement_items = statement.items()
        # Without a base (`push --force`) it's unknown what's on Polygon — nothing is cleared.
        tutorial_cleared = (
            TUTORIAL_FILENAME not in statement.tex_files
            and ctx.synced is not None
            and ctx.synced.get(statement.tutorial_key) is not None
        )
        if not (changed_items(ctx, statement_items) or tutorial_cleared):
            continue
        client.call(
            "problem.saveStatement",
            {
                "problemId": str(polygon_id),
                "lang": statement.language,
                "name": statement.name,
                **{
                    _tex_param(filename): content.decode("utf-8")
                    for filename, content in statement.tex_files.items()
                },
                **({TUTORIAL_PARAM: ""} if tutorial_cleared else {}),
            },
        )
        uploaded.update(statement_items)
        if tutorial_cleared:
            uploaded[statement.tutorial_key] = None
        done.append(f"saved {statement.language} statement (name={statement.name!r})")

    changed_examples: dict[str, str | None] = dict(changed_items(ctx, example_items))
    sent_examples = 0
    for example in examples:
        cleared = _cleared_fields(ctx, example)
        if not (set(example.items()) & set(changed_examples) or cleared):
            continue
        params = {
            "problemId": str(polygon_id),
            "testset": _TESTSET,
            "testIndex": str(example.index),
            "testInput": example.input.decode("utf-8"),
            "testUseInStatements": "true",
        }
        params.update(dict(example.fields.values()))
        params.update(dict(cleared.values()))
        client.call("problem.saveTest", params)
        changed_examples.update({key: None for key in cleared})
        sent_examples += 1
    if sent_examples:
        uploaded.update(changed_examples)
        done.append(f"{sent_examples} of {len(examples)} sample test(s)")

    if not uploaded:
        return StepResult(NO_CHANGES_MESSAGE)
    return StepResult(f"{' and '.join(done)} on Polygon id={polygon_id}", uploaded)


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`). While there's no `name.txt` (push
    creates it from the name in the spec), the name isn't compared."""

    def read_items() -> dict[str, str]:
        statement_dir = _statement_dir(problem_id, problems_dir)
        # There's no spec here: interaction.tex is compared if it exists, and there's no
        # mandatory language.
        statements = read_statements(statement_dir)
        items = _example_items(_read_examples(statement_dir))
        state = load_polygon_state(problem_id, problems_dir=problems_dir)
        synced = state.synced if state is not None else {}
        for statement in statements:
            items.update(statement.items())
            # Push clears a deleted tutorial on Polygon — that's an unsynced edit too.
            if statement.tutorial_key not in items and synced.get(statement.tutorial_key):
                items[statement.tutorial_key] = digest(b"")
        return items

    return sync_status(problem_id, problems_dir, key_prefix="statement/", read_items=read_items)


def read_statements(
    statement_dir: Path,
    *,
    language: str | None = None,
    interactive: bool | None = None,
    title: Title | None = None,
) -> list[Statement]:
    """The statement of each language `statement/<code>/`, alphabetically by code.

    `language` — the main language: without its directory — `PolygonStepError`.
    `interactive` — whether `interaction.tex` is needed; `None` (`polygon status`, without
    a spec) — it's read if it exists. With `title` (push) a missing `name.txt`
    is created from the spec's name in this language, and without a name —
    `PolygonStepError`; without `title` the name of such a statement is unknown
    (`Statement.name` — `None`).
    """
    try:
        codes = statement_languages(statement_dir)
    except ProblemPathError as exc:
        raise PolygonStepError(str(exc)) from exc
    if language is not None and language not in codes:
        raise PolygonStepError(
            f"directory {statement_dir / language} not found — first run "
            "the generative step statement_draft"
        )
    statements = []
    for code in codes:
        directory = statement_dir / code
        name_path = directory / NAME_FILENAME
        if not name_path.exists() and title is not None:
            default = title.for_language(code)
            if default is None:
                raise PolygonStepError(
                    f"{name_path} not found — write the problem name in "
                    f"{polygon_language(code)!r} into it"
                )
            name_path.write_text(default + "\n", encoding="utf-8")
        statements.append(
            Statement(
                code=code,
                tex_files=_read_statement_dir(
                    directory,
                    interactive=(
                        (directory / INTERACTION_FILENAME).exists()
                        if interactive is None
                        else interactive
                    ),
                    generated=code == language,
                ),
                name=read_name(name_path) if name_path.exists() else None,
            )
        )
    return statements


def read_name(path: Path) -> str:
    """The problem's name from `name.txt` without surrounding spaces and line
    breaks — editors add them, they aren't part of the name."""
    name = path.read_text(encoding="utf-8").strip()
    if not name:
        raise PolygonStepError(f"{path} is empty — write the problem name into it")
    return name


def _tex_param(filename: str) -> str:
    return {
        **_TEX_PARAM_NAMES,
        INTERACTION_FILENAME: INTERACTION_PARAM,
        TUTORIAL_FILENAME: TUTORIAL_PARAM,
    }[filename]


def _example_items(examples: list[Example]) -> dict[str, str]:
    return {key: value for example in examples for key, value in example.items().items()}


def _cleared_fields(ctx: StepContext, example: Example) -> dict[str, tuple[str, str]]:
    """The fields of an example test that are in the sync base but whose file
    no longer exists: key -> (parameter, the value on clearing). Without a base
    (`push --force`) it's unknown what was on Polygon — nothing is cleared."""
    if ctx.synced is None:
        return {}
    return {
        key: (param, empty)
        for _, key, param, empty in _optional_fields(example.index)
        if key not in example.fields and ctx.synced.get(key) is not None
    }


def _statement_dir(problem_id: str, problems_dir: Path) -> Path:
    return Path(problems_dir) / problem_id / STATEMENT_DIRNAME


def _read_statement_dir(
    statement_dir: Path, *, interactive: bool = False, generated: bool = True
) -> dict[str, bytes]:
    """The contents of the 4 fixed tex files (file name -> contents), for an
    interactive problem — also `interaction.tex`. If one is missing —
    `PolygonStepError` with its name, not an empty string instead of the contents.
    The optional `tutorial.tex` — only if it exists.
    `generated=False` — a directory of a non-main language: it's written not by a model step,
    but by the author (or `polygon pull`)."""
    names = [*_TEX_PARAM_NAMES, *([INTERACTION_FILENAME] if interactive else [])]
    missing = [name for name in names if not (statement_dir / name).exists()]
    if missing:
        hint = (
            "write them by hand (a statement in another language) or remove the directory"
            if not generated
            else "first run the generative step statement_draft"
        )
        raise PolygonStepError(f"files missing in {statement_dir}: {missing} — {hint}")
    if (statement_dir / TUTORIAL_FILENAME).exists():
        names.append(TUTORIAL_FILENAME)
    return {filename: (statement_dir / filename).read_bytes() for filename in names}


def _read_examples(statement_dir: Path) -> list[Example]:
    """The examples by ascending `testIndex` with the fields for the statement whose files
    exist (see `Example`). A verify flag other than `true` — `PolygonStepError`.

    The numbering of `example_<N>.txt` must be contiguous 1..N. The set of files
    is already checked by `statement_draft` before writing, but this step runs
    independently of it, so the check is repeated here.
    """
    examples_dir = statement_dir / EXAMPLES_DIRNAME
    if not examples_dir.is_dir():
        raise PolygonStepError(
            f"directory {examples_dir} not found — first run the generative step statement_draft"
        )

    found: dict[int, bytes] = {}
    for path in examples_dir.iterdir():
        match = _EXAMPLE_PATTERN.match(path.name)
        if match:
            found[int(match.group(1))] = path.read_bytes()

    if not found:
        raise PolygonStepError(f"no example_<N>.txt files in {examples_dir}")

    if set(found) != set(range(1, len(found) + 1)):
        raise PolygonStepError(
            f"example_<N>.txt numbering in {examples_dir} is not contiguous "
            f"1..{len(found)}: found indices {sorted(found)}"
        )

    examples: list[Example] = []
    for index, content in sorted(found.items()):
        fields: dict[str, tuple[str, str]] = {}
        for filename, key, param, _ in _optional_fields(index):
            path = examples_dir / filename
            if not path.exists():
                continue
            value = path.read_bytes()
            if key == verify_key(index):
                if value.strip().lower() != _VERIFY_VALUE:
                    raise PolygonStepError(
                        f"{path} must contain 'true' — delete the file to turn "
                        "verification of input/output for statements off"
                    )
                value = _VERIFY_VALUE
            fields[key] = (param, value.decode("utf-8"))
        examples.append(Example(index, content, fields))
    return examples


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
