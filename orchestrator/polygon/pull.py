"""Downloading a problem from Polygon into `problems/<problem_id>/` —
`orchestrator <problem> polygon pull [--overwrite]`.

The reverse direction to the polygon steps (`steps/`): binds `problem_id` to
a problem already existing on Polygon (`polygon_state.json`), compares with
Polygon each part of the problem the orchestrator uses, and
rewrites the sync base (`revision`/`synced`, see
`orchestrator/polygon/sync.py`) by what's on Polygon now. By
analogy with git — `git pull` with a three-way comparison: the common base of a local
file and the version on Polygon is its hash in the sync base before this pull
(`synced[key]`, what the last push uploaded or pull saw).

The decision is made for each file separately:

- there's no local file — it's downloaded (`downloaded`);
- matches Polygon — it isn't touched (`up to date`);
- differs, and the local file matches the base — only Polygon changed:
  the local file is replaced with the version from Polygon (`updated`, fast-forward);
- differs, and the version on Polygon matches the base — only the
  local file changed: the local version stays (`kept`) even with
  `--overwrite`, the next `polygon push` uploads it (the base records the
  hash of the version from Polygon — push compares the local one with it);
- differs, and both sides differ from the base — both changed (`conflict`),
  or there's no base for the file — who changed it is unknown (`differs`): without
  `--overwrite` the local version stays, with `--overwrite` — it's replaced with the
  version from Polygon (`overwritten`);
- exists only locally — it isn't touched (`local only`), and the next push
  uploads it (if it uploads such a part with this spec — then the condition
  is in `PullItem.push_condition`); pull never deletes local files.

A replaced local file (`updated`, `overwritten`) is first moved to
`.pull_backup/<time>/` (`problems/` is outside git, it has no other copy).
A differing file left local (`kept`, `conflict`, `differs`) gets in the
base the hash of the version from Polygon — for the next pull it's already an edit that's only
local, and `--overwrite` won't replace it. To discard the local edit
in this case, it's enough to delete the file locally — pull downloads it again.

Pull refuses to work if the problem's working copy on Polygon has
uncommitted edits: the sync base is always a committed revision.

Files that Polygon returns in a listing with a modification time (validator,
non-standard checker, generators, solutions, `problem_lib.h`, Attachments)
are downloaded only if the version on Polygon may differ from the one recorded in
the base. The version on Polygon is considered known — its hash equals `synced[key]` —
if the key has a hash in the base and either the stamp (`stamps`, see
`orchestrator/polygon/sync.py`) matches the listing, or the revision on
Polygon equals the one recorded in the base (nothing was committed on Polygon since the last sync;
revisions on Polygon only grow, so keys compared
even by a failed pull belong to the same revision). For a known version
the local file is compared by hash without downloading: matched — `up to date`,
differs — only the local file changed, `kept` without downloading. Stamps
are recorded together with the hashes for each file — a partially failed pull
leaves a base in which each "hash + stamp" pair is correct. The statement,
the examples, the TL/ML and the test-script are requested every time — one request
per part, they have no modification time.

A local spec (`problems/<problem_id>/authoring/spec.yaml`) isn't needed: the command is meant
also for problems that have nothing at all locally. The spec itself
is compared with Polygon as an ordinary file (see below), but isn't parsed or
validated.

What is compared with what (the names are the same that the polygon steps read):

- the statement (`problem.statements`) in each language -> `statement/<code>/`
  (`ru` for `russian`, see `orchestrator/statement_languages.py`):
  `legend.tex`, `input_format.tex`, `output_format.tex`, `notes.tex`.
  A section empty on Polygon is downloaded as an empty file — `upload_statement`
  requires all four. A language directory that isn't on Polygon — `local only`.
  The interaction protocol of an interactive problem -> `interaction.tex` of the same
  directory — only if it's non-empty on Polygon or the file exists locally.
  The tutorial -> `tutorial.tex` — likewise. The problem's name from the statement ->
  `name.txt` (compared without surrounding spaces and line breaks);
- tests with the "use in statements" flag (`problem.tests`, testset `tests`) ->
  `statement/examples/example_<K>.txt`, K is the ordinal number of such a test
  (1-based), not its index on Polygon; the input/output for statements
  (for an interactive problem — the example's dialogue) ->
  `example_<K>.input_for_statement.txt` and
  `example_<K>.output_for_statement.txt` — each on its own, only if
  the field exists on Polygon or the file locally; the "verify input/output for
  statements" flag -> `example_<K>.verify_for_statement.txt` (`true`; no flag
  — no file) likewise;
- aux files (the "Attachments" section, `problem.files` + `problem.viewFile`;
  uploaded by the `upload_attachments` step) -> `authoring/spec.yaml`,
  `authoring/constraints.yaml` (the whole `constraints_pick` result), the
  draft copy `authoring/draft<extension>` — each is compared only with its version in
  Attachments; other aux files aren't touched. A `constraints.yaml` of only
  TL/ML, written by previous versions of pull (`PULLED_CONSTRAINTS_HEADER`),
  gives way to the version from Attachments without `--overwrite`;
- TL/ML (`problem.info`) -> `limits.yaml` (the problem's actual TL/ML, see
  `orchestrator/limits.py`); they never touch `constraints.yaml`.
  The values are compared, not the file's text; the base is the hash of the TL/ML
  (`LIMITS_KEY`), the comparison is the same as for files. A replaced `limits.yaml`
  is written anew (`limits.render_limits`);
- the validator (`problem.validator` + `problem.viewFile`) -> `validator.cpp`;
- the checker (`problem.checker`) -> `checker.cpp` if it isn't standard
  (`std::...` — there's nothing to download);
- the interactor (`problem.interactor` — only if the problem on Polygon is
  interactive, by `problem.info`) -> `interactor.cpp`; the
  interactivity flag is recorded in the base (`INTERACTIVE_KEY`), so that push
  sets it by the spec if they diverge;
- the other source files `*.cpp` (`problem.files`) -> `generators/`
  (`generator_source_names`: everything that isn't the validator, the checker or the
  interactor);
- the test-script (`problem.script`, testset `tests`) -> `test_script` (or an
  already existing local `test_script_groups`); compared without empty lines
  (`normalize_script`) — Polygon drops them on saving;
- solutions (`problem.solutions` + `problem.viewSolution`) -> `solutions/`.
  A solution's tag on Polygon isn't saved locally: `upload_solutions` derives
  the tag from the file name, so names not following its scheme get a warning;
- the resource `problem_lib.h` -> `problem_lib.h` (the problem's copy, see
  `PROBLEM_LIB_FILENAME` in `orchestrator/steps/base.py`; if it isn't on
  Polygon, push uploads the local one, and without it — the template).

Text is compared up to line breaks (`\\r\\n` = `\\n`).
NOTE: it isn't verified on the real API whether Polygon normalizes the contents
in some other way (e.g. the trailing line break) — if it does, such files
will always be `kept`, and push will upload them every time.

A Polygon API error when downloading one part doesn't stop the rest, but
then the revision in the base isn't updated (`PullResult.ok` is false).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.limits import LIMITS_FILENAME, render_limits
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
    update_sync,
)
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.set_constraints import read_limits
from orchestrator.polygon.steps.upload_solutions import _parse_solution_filename
from orchestrator.polygon.steps.upload_statement import (
    INTERACTION_FILENAME,
    INTERACTION_PARAM,
    NAME_FILENAME,
    TUTORIAL_PARAM,
    dialogue_filenames,
    verify_filename,
)
from orchestrator.polygon.steps.upload_test_script import (
    SCRIPT_FILENAMES,
    normalize_script,
)
from orchestrator.polygon.sync import (
    CHECKER_FILE_KEY,
    CHECKER_KEY,
    CONSTRAINTS_KEY,
    INTERACTIVE_KEY,
    INTERACTOR_FILE_KEY,
    INTERACTOR_FILENAME,
    INTERACTOR_KEY,
    LIMITS_KEY,
    PROBLEM_LIB_KEY,
    PULLED_CONSTRAINTS_HEADER,
    SCRIPT_KEY,
    SPEC_KEY,
    STANDARD_CHECKER_PREFIX,
    TESTS_PLAN_KEY,
    VALIDATOR_FILE_KEY,
    VALIDATOR_KEY,
    digest,
    fetch_revision,
    generator_source_names,
    interactive_digest,
    is_attachment_name,
    is_draft_name,
    limits_digest,
    remote_stamps,
    statement_name_key,
)
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    PROBLEMS_DIR,
    TUTORIAL_DRAFT_FILENAME,
    TUTORIAL_FILENAME,
    ProblemPathError,
    authoring_dir,
    check_new_problem_location,
    is_removed_mark,
    problem_id_for,
)
from orchestrator.statement_languages import (
    STATEMENT_DIRNAME,
    is_language_dirname,
    language_code,
    polygon_language,
    statement_languages,
)
from orchestrator.steps.base import PROBLEM_LIB_FILENAME

logger = logging.getLogger(__name__)

# PullItem.status statuses:
ITEM_DOWNLOADED = "downloaded"  # wasn't there locally — downloaded
ITEM_SAME = "up to date"  # matches Polygon — not touched
ITEM_UPDATED = "updated"  # changed only on Polygon: replaced, the old one is in the backup
ITEM_OVERWRITTEN = "overwritten"  # changed locally too, --overwrite: replaced, old one backed up
ITEM_KEPT = "kept"  # differs from Polygon, the local version is kept
ITEM_CONFLICT = "conflict"  # changed both locally and on Polygon: the local version is kept
ITEM_DIFFERS = "differs"  # differs, no base — who changed it is unknown: the local version is kept
ITEM_LOCAL_ONLY = "local only"  # exists only locally — not touched
ITEM_ABSENT = "absent"  # neither on Polygon nor locally
ITEM_ERROR = "error"

_TESTSET = "tests"
# The contents of the verify input/output for statements flag file.
_VERIFY_CONTENT = b"true\n"

# A Statement object field -> a file in `statement/` (the reverse of
# `upload_statement._TEX_PARAM_NAMES`).
_STATEMENT_FILES = {
    "legend": "legend.tex",
    "input": "input_format.tex",
    "output": "output_format.tex",
    "notes": "notes.tex",
}

_VALIDATOR_FILENAME = VALIDATOR_FILE_KEY
_CHECKER_FILENAME = CHECKER_FILE_KEY
_BACKUP_DIRNAME = ".pull_backup"

# `detail` — only what the status doesn't say itself (where the version on Polygon came from,
# how the TL/ML differ): the CLI prints it for conflicts and errors.
_NOT_DOWNLOADED_DETAIL = "unchanged on Polygon since the last sync, not downloaded"
# Push uploads the protocol and the interactor only for an interactive problem.
_INTERACTIVE_CONDITION = "the spec has an interactor section"


@dataclass
class PullItem:
    """What happened to one part of the problem (usually — to one file)."""

    name: str  # the path relative to `problems/<problem_id>/`
    status: str
    detail: str = ""
    # For `local only`: with which spec push uploads the file; empty — it always uploads it.
    push_condition: str = ""


@dataclass
class PullResult:
    problem_id: str
    polygon_id: int
    link_detail: str
    revision: int | None = None
    backup_dir: Path | None = None  # where the overwritten files were moved, if there were any
    items: list[PullItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.status != ITEM_ERROR for item in self.items)


def pull_problem(
    problem_id: str,
    *,
    polygon_id: int | None = None,
    overwrite: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    on_part: Callable[[str], None] | None = None,
) -> PullResult:
    """Binds `problem_id` to a problem on Polygon, compares the
    local files with it and rewrites the sync base (see the module
    docstring).

    `problem_id` — the path of the problem directory inside the root `problems_dir` (see
    `orchestrator/problem_paths.py`). Which problem on Polygon: the one already
    recorded in `polygon_state.json`; otherwise a search for a non-deleted problem
    named `problem_id`, derived from the path (`problem_id_for`), and with the id
    `polygon_id`, if given (in case of several problems with one
    name). An unbound problem is set up only where
    `check_new_problem_location` allows it. If
    the problem couldn't be determined or its working copy has
    uncommitted edits — `PolygonStepError`, the files aren't touched.
    A `PolygonApiError` at this stage is also propagated outward.

    `on_part` is called with the part's name before comparing it — so that the CLI
    shows progress as it goes.
    """
    client = client or PolygonClient()
    resolved_id, link_detail = _resolve_and_link(problem_id, polygon_id, Path(problems_dir), client)
    logger.info("[pull] %s", link_detail)

    current = fetch_revision(client, resolved_id)
    if current.modified:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={resolved_id} has uncommitted changes in "
            "its working copy — commit or discard them on Polygon first"
        )

    result = PullResult(
        problem_id=problem_id,
        polygon_id=resolved_id,
        link_detail=link_detail,
        revision=current.revision,
    )
    problem_dir = Path(problems_dir) / problem_id
    base = load_polygon_state(problem_id, problems_dir=problems_dir)
    puller = _Puller(
        client=client,
        polygon_id=resolved_id,
        problem_dir=problem_dir,
        overwrite=overwrite,
        backup_dir=problem_dir
        / _BACKUP_DIRNAME
        / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        result=result,
        base_synced=dict(base.synced),
        base_stamps=dict(base.stamps),
        revision_unchanged=base.revision is not None and base.revision == current.revision,
    )
    for name, part in (
        ("statement", puller.pull_statement),
        ("statement/examples", puller.pull_examples),
        ("attachments", puller.pull_attachments),
        (LIMITS_FILENAME, puller.pull_limits),
        (PROBLEM_LIB_FILENAME, puller.pull_problem_lib),
        (_VALIDATOR_FILENAME, puller.pull_validator),
        (_CHECKER_FILENAME, puller.pull_checker),
        (INTERACTOR_FILENAME, puller.pull_interactor),
        ("generators", puller.pull_generators),
        ("test_script", puller.pull_script),
        ("solutions", puller.pull_solutions),
    ):
        if on_part is not None:
            on_part(name)
        try:
            part()
        except PolygonApiError as exc:
            puller.add(name, ITEM_ERROR, str(exc))

    # We always write the base for the successfully compared parts, the revision — only
    # if all of them were compared: otherwise push mustn't consider the base complete.
    update_sync(
        problem_id,
        synced=puller.synced,
        stamps=puller.stamps,
        revision=current.revision if result.ok else base.revision,
        problems_dir=problems_dir,
    )
    if puller.backed_up:
        result.backup_dir = puller.backup_dir
    return result


def _resolve_and_link(
    problem_id: str, polygon_id: int | None, problems_dir: Path, client: PolygonClient
) -> tuple[int, str]:
    """(Polygon problemId, a message about the binding). If there was no binding yet —
    creates `polygon_state.json`."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is not None:
        if polygon_id is not None and polygon_id != state.polygon_id:
            raise PolygonStepError(
                f"'{problem_id}': already linked to Polygon id={state.polygon_id} "
                f"(polygon_state.json), but --polygon-id={polygon_id} was given — "
                "remove polygon_state.json to relink"
            )
        return state.polygon_id, f"already linked to Polygon id={state.polygon_id}"

    try:
        check_new_problem_location(problems_dir, problem_id)
    except ProblemPathError as exc:
        raise PolygonStepError(str(exc)) from exc
    name = problem_id_for(problems_dir, problem_id)

    # The name is always passed, the numeric id — if given: `--polygon-id` only
    # narrows the search, it doesn't cancel the name check. The `name` filter of
    # `problems.list` may turn out inexact (not verified on the real
    # API), and we don't need deleted problems — we keep only exact
    # matches among the non-deleted ones.
    params = {"name": name, "showDeleted": "false"}
    if polygon_id is not None:
        params["id"] = str(polygon_id)
    searched = f"named {name!r}" + (f" with id={polygon_id}" if polygon_id else "")
    found = [
        item
        for item in client.call("problems.list", params) or []
        if item.get("name") == name
        and not item.get("deleted")
        and polygon_id in (None, item.get("id"))
    ]
    if not found:
        raise PolygonStepError(
            f"'{problem_id}': no Polygon problem {searched} is available to this API key"
        )
    if len(found) > 1:
        candidates = ", ".join(f"{item['id']} (owner {item.get('owner')})" for item in found)
        raise PolygonStepError(
            f"'{problem_id}': several Polygon problems are {searched}: {candidates} "
            "— pick one with --polygon-id N"
        )
    polygon_id = found[0]["id"]
    detail = f"found Polygon problem {searched} (id={polygon_id}), linked"

    save_polygon_state(
        problem_id,
        PolygonState(
            problem_id=name,
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        problems_dir=problems_dir,
    )
    return polygon_id, detail


@dataclass
class _Puller:
    """Comparing the parts of one problem; each `pull_*` is one part.

    `synced` accumulates the changes of the sync base: key -> the sha256 of the version on
    Polygon (up to normalization — of what would go locally), or
    `None` — the version on Polygon is unknown or doesn't exist, push must upload.
    `stamps` — the version stamps on Polygon for the same keys (see
    `update_sync`). `base_*` and `revision_unchanged` — the base before this pull,
    it decides which files may be left undownloaded (see the module docstring)."""

    client: PolygonClient
    polygon_id: int
    problem_dir: Path
    overwrite: bool
    backup_dir: Path
    result: PullResult
    base_synced: dict[str, str] = field(default_factory=dict)
    base_stamps: dict[str, str] = field(default_factory=dict)
    revision_unchanged: bool = False
    synced: dict[str, str | None] = field(default_factory=dict)
    stamps: dict[str, str | None] = field(default_factory=dict)
    backed_up: bool = False
    _files: dict | None = None
    _solutions: list | None = None
    _assigned: dict[str, str | None] = field(default_factory=dict)
    _remote_stamps: dict[str, str] | None = None
    _problem_info: dict | None = None

    def add(self, name: str, status: str, detail: str = "", *, push_condition: str = "") -> None:
        self.result.items.append(PullItem(name, status, detail, push_condition))
        level = logging.ERROR if status == ITEM_ERROR else logging.INFO
        logger.log(level, "[pull] %s: %s%s", name, status, f" — {detail}" if detail else "")

    def warn(self, message: str) -> None:
        self.result.warnings.append(message)
        logger.warning("%s", message)

    def pull_statement(self) -> None:
        """The statement of each Polygon language -> `statement/<code>/` (`language_code`);
        directories of languages that aren't on Polygon — `local only`."""
        statements = self._call("problem.statements") or {}
        remote: dict[str, dict] = {}
        for language, statement in statements.items():
            code = language_code(language)
            if not is_language_dirname(code):
                self.warn(
                    f"the {language!r} statement on Polygon is not pulled: "
                    "the language name is not a directory name of statement/"
                )
                continue
            remote[code] = statement
        try:
            local = set(statement_languages(self.problem_dir / STATEMENT_DIRNAME))
        except ProblemPathError as exc:
            self.warn(f"{exc} (its files are not compared)")
            local = set()
        if not remote and not local:
            self.add(STATEMENT_DIRNAME, ITEM_ABSENT, "no statements on Polygon")
        for code in sorted(set(remote) | local):
            self._pull_statement(code, remote.get(code))

    def _pull_statement(self, code: str, statement: dict | None) -> None:
        """The statement in the language `code` (a Statement object, `None` — it isn't on Polygon,
        but the language directory exists locally) -> `statement/<code>/`."""
        prefix = f"{STATEMENT_DIRNAME}/{code}/"
        name_file = f"{prefix}{NAME_FILENAME}"
        name_key = statement_name_key(code)
        tutorial_file = f"{prefix}{TUTORIAL_FILENAME}"
        if statement is None:
            absent = f"no {polygon_language(code)!r} statement on Polygon"
            for filename in _STATEMENT_FILES.values():
                self._missing_if_local(f"{prefix}{filename}", absent)
            self._missing_if_local(name_file, absent, key=name_key)
            self._missing_if_local(
                f"{prefix}{INTERACTION_FILENAME}",
                absent,
                push_condition=_INTERACTIVE_CONDITION,
            )
            self._missing_if_local(tutorial_file, absent)
            return
        for field_name, filename in _STATEMENT_FILES.items():
            self._sync_file(
                f"{prefix}{filename}",
                lambda field_name=field_name: (statement.get(field_name) or "").encode("utf-8"),
            )
        interaction = statement.get(INTERACTION_PARAM) or ""
        interaction_file = f"{prefix}{INTERACTION_FILENAME}"
        if interaction:
            self._sync_file(interaction_file, lambda: interaction.encode("utf-8"))
        else:
            self._missing_if_local(
                interaction_file,
                "no interaction protocol on Polygon",
                push_condition=_INTERACTIVE_CONDITION,
            )
        tutorial = statement.get(TUTORIAL_PARAM) or ""
        if tutorial:
            self._sync_file(tutorial_file, lambda: tutorial.encode("utf-8"))
        else:
            self._missing_if_local(tutorial_file, "no tutorial on Polygon")
        name = (statement.get("name") or "").strip()
        if not name:
            self._missing_on_polygon(
                name_file, "no problem name in the statement on Polygon", key=name_key
            )
            return
        self._sync_file(
            name_file,
            lambda: f"{name}\n".encode(),
            key=name_key,
            # Like `upload_statement.read_name`: the hash and the comparison — by the name itself.
            normalize=lambda content: content.decode("utf-8").strip().encode("utf-8"),
        )

    def pull_examples(self) -> None:
        tests = self._call("problem.tests", {"testset": _TESTSET}) or []
        statement_tests = sorted(
            (test for test in tests if test.get("useInStatements")),
            key=lambda test: test["index"],
        )
        for number, test in enumerate(statement_tests, start=1):
            self._sync_file(
                f"statement/examples/example_{number}.txt",
                # Generated (not manual) tests have no `input` field.
                lambda test=test: (
                    test["input"].encode("utf-8")
                    if test.get("input") is not None
                    else self._call_raw(
                        "problem.testInput",
                        {"testset": _TESTSET, "testIndex": str(test["index"])},
                    )
                ),
                detail=f"Polygon test {test['index']}",
            )
            for filename, field_name in zip(
                dialogue_filenames(number),
                ("inputForStatement", "outputForStatement"),
                strict=True,
            ):
                value = test.get(field_name) or ""
                name = f"statement/examples/{filename}"
                if value:
                    self._sync_file(
                        name,
                        lambda value=value: value.encode("utf-8"),
                        detail=f"Polygon test {test['index']}",
                    )
                else:
                    self._missing_if_local(name, f"no {field_name} in Polygon test {test['index']}")
            verify_name = f"statement/examples/{verify_filename(number)}"
            if test.get("verifyInputOutputForStatements"):
                self._sync_file(
                    verify_name,
                    lambda: _VERIFY_CONTENT,
                    detail=f"Polygon test {test['index']}",
                    # Like `upload_statement._read_examples`: the value is `true`.
                    normalize=lambda content: content.strip().lower(),
                )
            else:
                self._missing_if_local(
                    verify_name,
                    f"verifyInputOutputForStatements is off in Polygon test {test['index']}",
                )

        examples_dir = self.problem_dir / "statement" / "examples"
        pulled = {
            filename
            for n in range(1, len(statement_tests) + 1)
            for filename in (f"example_{n}.txt", *dialogue_filenames(n), verify_filename(n))
        }
        local_extra = sorted(
            path.name
            for path in (examples_dir.glob("example_*.txt") if examples_dir.is_dir() else [])
            if path.name not in pulled
        )
        for filename in local_extra:
            self._missing_on_polygon(f"statement/examples/{filename}")
        if not statement_tests and not local_extra:
            self.add("statement/examples", ITEM_ABSENT, "no tests used in statements on Polygon")

    def pull_limits(self) -> None:
        info = self._info()
        time_limit_ms, memory_limit_mb = info.get("timeLimit"), info.get("memoryLimit")
        if time_limit_ms is None or memory_limit_mb is None:
            self._missing_on_polygon(
                LIMITS_FILENAME, "no timeLimit/memoryLimit on Polygon", key=LIMITS_KEY
            )
            return
        remote = (int(time_limit_ms), int(memory_limit_mb))
        content = render_limits(*remote).encode("utf-8")

        path = self.problem_dir / LIMITS_FILENAME
        if not path.exists():
            self._write(LIMITS_FILENAME, content)
            self.add(LIMITS_FILENAME, ITEM_DOWNLOADED)
            self.synced[LIMITS_KEY] = limits_digest(*remote)
            return

        try:
            local = read_limits(self.problem_dir.name, self.problem_dir.parent)
        except PolygonStepError:
            local = None
        if local == remote:
            self.add(LIMITS_FILENAME, ITEM_SAME)
            self.synced[LIMITS_KEY] = limits_digest(*remote)
            return

        differs = f"local {_describe_limits(local)}, Polygon {_describe_limits(remote)}"
        change = _change(
            self.base_synced.get(LIMITS_KEY),
            limits_digest(*local) if local is not None else None,
            limits_digest(*remote),
        )
        if change == _LOCAL:
            self.add(LIMITS_FILENAME, ITEM_KEPT, differs)
            self.synced[LIMITS_KEY] = None
            return
        if change == _REMOTE or self.overwrite:
            self._backup(LIMITS_FILENAME)
            self._write(LIMITS_FILENAME, content)
            self.add(
                LIMITS_FILENAME, ITEM_UPDATED if change == _REMOTE else ITEM_OVERWRITTEN, differs
            )
            self.synced[LIMITS_KEY] = limits_digest(*remote)
            return
        self.add(LIMITS_FILENAME, ITEM_CONFLICT if change == _BOTH else ITEM_DIFFERS, differs)
        self.synced[LIMITS_KEY] = None

    def pull_problem_lib(self) -> None:
        resources = {file["name"] for file in self._polygon_files().get("resourceFiles") or []}
        if PROBLEM_LIB_FILENAME not in resources:
            self._missing_on_polygon(
                PROBLEM_LIB_FILENAME,
                "no resource file on Polygon",
                key=PROBLEM_LIB_KEY,
            )
            return
        self._sync_file(
            PROBLEM_LIB_FILENAME,
            lambda: self._call_raw(
                "problem.viewFile", {"type": "resource", "name": PROBLEM_LIB_FILENAME}
            ),
            key=PROBLEM_LIB_KEY,
            stamped=True,
        )

    def pull_validator(self) -> None:
        polygon_name = self._assigned_name("problem.validator")
        if not polygon_name:
            self._missing_on_polygon(
                _VALIDATOR_FILENAME, "no validator set on Polygon", key=VALIDATOR_FILE_KEY
            )
            self.synced[VALIDATOR_KEY] = None
            return
        self._sync_file(
            _VALIDATOR_FILENAME,
            lambda: self._view_source(polygon_name),
            key=VALIDATOR_FILE_KEY,
            also={VALIDATOR_KEY: digest(polygon_name)},
            detail=f"Polygon source file {polygon_name}",
            stamped=True,
        )

    def pull_checker(self) -> None:
        polygon_name = self._assigned_name("problem.checker")
        if not polygon_name:
            self._missing_on_polygon(
                _CHECKER_FILENAME, "no checker set on Polygon", key=CHECKER_FILE_KEY
            )
            self.synced[CHECKER_KEY] = None
        elif polygon_name.startswith(STANDARD_CHECKER_PREFIX):
            self._missing_on_polygon(
                _CHECKER_FILENAME,
                f"standard checker {polygon_name} on Polygon",
                key=CHECKER_FILE_KEY,
                push_condition="checker.custom_needed: true",
            )
            self.synced[CHECKER_KEY] = digest(polygon_name)
        else:
            self._sync_file(
                _CHECKER_FILENAME,
                lambda: self._view_source(polygon_name),
                key=CHECKER_FILE_KEY,
                also={CHECKER_KEY: digest(polygon_name)},
                detail=f"Polygon source file {polygon_name}",
                stamped=True,
            )

    def pull_interactor(self) -> None:
        interactive = bool(self._info().get("interactive"))
        # A normal problem — no key: push compares the flag only if it's
        # in the base or the spec is interactive (see `set_constraints`).
        self.synced[INTERACTIVE_KEY] = interactive_digest(True) if interactive else None
        if not interactive:
            self.synced[INTERACTOR_KEY] = None
            self._missing_if_local(
                INTERACTOR_FILENAME,
                "the problem is not interactive on Polygon",
                key=INTERACTOR_FILE_KEY,
                push_condition=_INTERACTIVE_CONDITION,
            )
            return
        polygon_name = self._interactor_name()
        if not polygon_name:
            self._missing_on_polygon(
                INTERACTOR_FILENAME, "no interactor set on Polygon", key=INTERACTOR_FILE_KEY
            )
            self.synced[INTERACTOR_KEY] = None
            return
        self._sync_file(
            INTERACTOR_FILENAME,
            lambda: self._view_source(polygon_name),
            key=INTERACTOR_FILE_KEY,
            also={INTERACTOR_KEY: digest(polygon_name)},
            detail=f"Polygon source file {polygon_name}",
            stamped=True,
        )

    def pull_generators(self) -> None:
        names = generator_source_names(
            self._polygon_files().get("sourceFiles") or [],
            self._assigned_name("problem.validator"),
            self._assigned_name("problem.checker"),
            self._interactor_name(),
        )
        for polygon_name in names:
            name = f"generators/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
            else:
                self._sync_file(
                    name,
                    lambda polygon_name=polygon_name: self._view_source(polygon_name),
                    stamped=True,
                )

        local_only = self._local_only("generators", set(names), pattern="*.cpp")
        if not names and not local_only:
            self.add("generators", ITEM_ABSENT, "no generator source files on Polygon")

    def pull_script(self) -> None:
        existing = [name for name in SCRIPT_FILENAMES if (self.problem_dir / name).exists()]
        local_name = existing[0] if existing else SCRIPT_FILENAMES[0]
        content = self._call_raw("problem.script", {"testset": _TESTSET})
        if not content.strip():
            self._missing_on_polygon(local_name, "empty test script on Polygon", key=SCRIPT_KEY)
            return
        # Like `upload_test_script`: empty lines that Polygon drops don't count.
        self._sync_file(local_name, lambda: content, key=SCRIPT_KEY, normalize=normalize_script)

    def pull_solutions(self) -> None:
        solutions = sorted(self._polygon_solutions(), key=lambda solution: solution["name"])
        for solution in solutions:
            polygon_name = solution["name"]
            name = f"solutions/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
                continue
            written = self._sync_file(
                name,
                lambda polygon_name=polygon_name: self._call_raw(
                    "problem.viewSolution", {"name": polygon_name}
                ),
                detail=f"Polygon tag {solution.get('tag')}",
                stamped=True,
            )
            if written:
                self._warn_if_unparseable_solution_name(polygon_name, solution.get("tag"))

        remote_names = {solution["name"] for solution in solutions}
        local_only = self._local_only("solutions", remote_names, pattern="*")
        if not solutions and not local_only:
            self.add("solutions", ITEM_ABSENT, "no solutions on Polygon")

    def pull_attachments(self) -> None:
        """The problem's aux files (`is_attachment_name`: the spec, `constraints.yaml`,
        `tests_plan.yaml`, the draft copy, the tutorial draft), uploaded by the `upload_attachments` step; locally they are
        in `authoring/`, the base key is the name on Polygon. Other aux files on
        Polygon aren't touched."""
        remote_names = sorted(
            file["name"]
            for file in self._polygon_files().get("auxFiles") or []
            if is_attachment_name(file["name"])
        )
        for name in remote_names:
            if name == CONSTRAINTS_KEY:
                self._drop_pulled_constraints()
            self._sync_file(
                _authoring_name(name),
                lambda name=name: self._call_raw("problem.viewFile", {"type": "aux", "name": name}),
                key=name,
                stamped=True,
            )

        if SPEC_KEY not in remote_names:
            self._missing_on_polygon(
                _authoring_name(SPEC_KEY), "no attachment on Polygon", key=SPEC_KEY
            )
        if CONSTRAINTS_KEY not in remote_names:
            path = self.problem_dir / _authoring_name(CONSTRAINTS_KEY)
            if path.exists() and _is_pulled_constraints(path.read_bytes()):
                # A file of only TL/ML from previous versions of pull isn't the result of
                # `constraints_pick`, `upload_attachments` doesn't upload it.
                self.synced[CONSTRAINTS_KEY] = None
            else:
                self._missing_on_polygon(
                    _authoring_name(CONSTRAINTS_KEY),
                    "no attachment on Polygon",
                    key=CONSTRAINTS_KEY,
                )
        if TESTS_PLAN_KEY not in remote_names:
            self._missing_if_local(
                _authoring_name(TESTS_PLAN_KEY), "no attachment on Polygon", key=TESTS_PLAN_KEY
            )
        if TUTORIAL_DRAFT_FILENAME not in remote_names:
            self._missing_if_local(
                _authoring_name(TUTORIAL_DRAFT_FILENAME),
                "no attachment on Polygon",
                key=TUTORIAL_DRAFT_FILENAME,
            )
        directory = authoring_dir(self.problem_dir)
        local_drafts = sorted(
            path.name
            for path in (directory.iterdir() if directory.is_dir() else [])
            if path.is_file() and is_draft_name(path.name) and path.name not in remote_names
        )
        for name in local_drafts:
            self._missing_on_polygon(_authoring_name(name), "no attachment on Polygon", key=name)

    def _drop_pulled_constraints(self) -> None:
        """A local `constraints.yaml` of only TL/ML, assembled by previous
        versions of pull, gives way to the `constraints_pick` result from Attachments: to the backup and
        deleted, `_sync_file` downloads the file from Polygon."""
        name = _authoring_name(CONSTRAINTS_KEY)
        path = self.problem_dir / name
        if path.exists() and _is_pulled_constraints(path.read_bytes()):
            self._backup(name)
            path.unlink()

    def _warn_if_unparseable_solution_name(self, polygon_name: str, tag: object) -> None:
        try:
            _parse_solution_filename(Path(polygon_name))
        except PolygonStepError:
            self.warn(
                f"solutions/{polygon_name}: the name does not follow "
                "<verdict>_<language>_<description>_<author>.<ext>, so upload_solutions "
                f"will refuse to run until it is renamed (tag on Polygon: {tag})"
            )

    def _sync_file(
        self,
        name: str,
        fetch: Callable[[], bytes],
        *,
        key: str | None = None,
        also: dict[str, str] | None = None,
        detail: str = "",
        normalize: Callable[[bytes], bytes] = lambda content: content,
        stamped: bool = False,
    ) -> bool:
        """Compares the local file `name` with the version on Polygon (`fetch`) and
        writes it to disk if needed (see the module docstring). The base key is
        `key` (`name` by default); the additional entries `also` are written
        only if the file now matches Polygon. `normalize` is applied
        to the contents before comparing and hashing.

        `stamped` — the file has a stamp in the Polygon listing: it's written to the
        base together with the hash, and if the version on Polygon is already known from the base,
        the file isn't downloaded until it needs to be written to disk.

        Returns whether the file was written. An API error — an "error" entry for this
        file, the other files of the same part continue to be processed."""
        key = key or name
        also = also or {}
        stamp = self._remote_stamp(key) if stamped else None
        path = self.problem_dir / name

        known = self._known_remote_digest(key, stamp) if stamped else None
        if known is not None and path.exists():
            local = digest(normalize(path.read_bytes()))
            if local == known:
                self.add(name, ITEM_SAME, _join(detail, _NOT_DOWNLOADED_DETAIL))
                self._record(key, known, stamp, also)
                return False
            # The version on Polygon equals the base — only the local file changed.
            self.add(name, ITEM_KEPT, detail)
            self._record(key, known, stamp, {k: None for k in also})
            return False

        try:
            remote = fetch()
        except PolygonApiError as exc:
            self.add(name, ITEM_ERROR, str(exc))
            self._record(key, None, None, {k: None for k in also})
            return False

        written = False
        if not path.exists():
            self._write(name, remote)
            self.add(name, ITEM_DOWNLOADED, detail)
            written = True
        elif _same(normalize(path.read_bytes()), normalize(remote)):
            self.add(name, ITEM_SAME, detail)
        else:
            change = _change(
                self.base_synced.get(key),
                digest(normalize(path.read_bytes())),
                digest(normalize(remote)),
            )
            if change == _REMOTE or (change != _LOCAL and self.overwrite):
                self._backup(name)
                self._write(name, remote)
                self.add(name, ITEM_UPDATED if change == _REMOTE else ITEM_OVERWRITTEN, detail)
                written = True
            else:
                status = {_LOCAL: ITEM_KEPT, _BOTH: ITEM_CONFLICT}.get(change, ITEM_DIFFERS)
                self.add(name, status, detail)
                self._record(key, digest(normalize(remote)), stamp, {k: None for k in also})
                return False

        self._record(key, digest(normalize(path.read_bytes())), stamp, also)
        return written

    def _record(
        self, key: str, value: str | None, stamp: str | None, also: dict[str, str | None]
    ) -> None:
        """Writes the hash of `key` to the base together with its stamp and the `also` entries."""
        self.synced[key] = value
        self.stamps[key] = stamp
        self.synced.update(also)

    def _known_remote_digest(self, key: str, stamp: str | None) -> str | None:
        """The hash of the version of `key` on Polygon if it's known from the base without
        downloading (see the module docstring), otherwise `None`."""
        base = self.base_synced.get(key)
        if base is None:
            return None
        if self.revision_unchanged or (stamp is not None and self.base_stamps.get(key) == stamp):
            return base
        return None

    def _remote_stamp(self, key: str) -> str | None:
        if self._remote_stamps is None:
            self._remote_stamps = remote_stamps(
                self._listing(self._polygon_files, {}),
                self._listing(self._polygon_solutions, []),
                self._listing(lambda: self._assigned_name("problem.validator"), None),
                self._listing(lambda: self._assigned_name("problem.checker"), None),
                self._listing(self._interactor_name, None),
            )
        return self._remote_stamps.get(key)

    def _listing(self, fetch: Callable[[], object], fallback: object) -> object:
        """The listing for stamps; if it failed — `fallback`: files without stamps are simply
        downloaded, and the error itself surfaces in the part that needs the
        listing (its keys aren't touched then)."""
        try:
            return fetch()
        except PolygonApiError as exc:
            logger.warning("[pull] no modification times from this listing: %s", exc)
            return fallback

    def _missing_on_polygon(
        self,
        name: str,
        detail: str = "",
        *,
        key: str | None = None,
        push_condition: str = "",
    ) -> None:
        """The part `name` isn't on Polygon: if it exists locally — "local only"
        (push uploads it — always or with `push_condition`), otherwise "absent".
        It mustn't be in the base."""
        self.synced[key or name] = None
        if (self.problem_dir / name).exists():
            self.add(name, ITEM_LOCAL_ONLY, detail, push_condition=push_condition)
        else:
            self.add(name, ITEM_ABSENT, detail)

    def _missing_if_local(
        self, name: str, detail: str, *, key: str | None = None, push_condition: str = ""
    ) -> None:
        """Like `_missing_on_polygon`, but for parts that not all
        problems have (the protocol and the interactor of an interactive problem): if the
        file doesn't exist locally either, no report about it is printed."""
        if (self.problem_dir / name).exists():
            self._missing_on_polygon(name, detail, key=key, push_condition=push_condition)
        else:
            self.synced[key or name] = None

    def _local_only(self, subdir: str, remote_names: set[str], *, pattern: str) -> list[str]:
        """The files of `subdir/` (by `pattern`) that aren't on Polygon — "local only".
        Those marked by a step as removed (`is_removed_mark`) aren't part of the problem."""
        directory = self.problem_dir / subdir
        names = sorted(
            path.name
            for path in (directory.glob(pattern) if directory.is_dir() else [])
            if path.is_file() and not is_removed_mark(path) and path.name not in remote_names
        )
        for filename in names:
            self._missing_on_polygon(f"{subdir}/{filename}")
        return names

    def _backup(self, name: str) -> None:
        target = self.backup_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((self.problem_dir / name).read_bytes())
        self.backed_up = True

    def _write(self, name: str, content: bytes) -> None:
        path = self.problem_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _polygon_files(self) -> dict:
        if self._files is None:
            self._files = self._call("problem.files") or {}
        return self._files

    def _polygon_solutions(self) -> list:
        if self._solutions is None:
            self._solutions = self._call("problem.solutions") or []
        return self._solutions

    def _info(self) -> dict:
        if self._problem_info is None:
            self._problem_info = self._call("problem.info") or {}
        return self._problem_info

    def _interactor_name(self) -> str | None:
        """The name of the assigned interactor; for a normal problem — `None`, without
        requesting `problem.interactor` (it has no interactor)."""
        if not self._info().get("interactive"):
            return None
        return self._assigned_name("problem.interactor")

    def _assigned_name(self, method_name: str) -> str | None:
        """The name of the assigned validator/checker (`problem.validator`/`problem.checker`)."""
        if method_name not in self._assigned:
            self._assigned[method_name] = self._call(method_name)
        return self._assigned[method_name]

    def _view_source(self, polygon_name: str) -> bytes:
        return self._call_raw("problem.viewFile", {"type": "source", "name": polygon_name})

    def _call(self, method_name: str, params: dict[str, str] | None = None):
        return self.client.call(method_name, {"problemId": str(self.polygon_id), **(params or {})})

    def _call_raw(self, method_name: str, params: dict[str, str]) -> bytes:
        return self.client.call_raw(method_name, {"problemId": str(self.polygon_id), **params})


# Who changed the differing local version and the version on Polygon since the last
# sync (`_change`).
_LOCAL = "local"
_REMOTE = "remote"
_BOTH = "both"


def _change(base: str | None, local: str | None, remote: str) -> str | None:
    """A three-way comparison of differing versions by hashes: `_LOCAL` — only the local one
    differs from the base, `_REMOTE` — only the version on Polygon,
    `_BOTH` — both; `None` — there's no base (or local hash), who changed it is
    unknown."""
    if base is None or local is None:
        return None
    if local == base:
        return _REMOTE
    if remote == base:
        return _LOCAL
    return _BOTH


def _authoring_name(attachment: str) -> str:
    """The path of an Attachments file in the problem directory (`authoring/<name>`)."""
    return f"{AUTHORING_DIRNAME}/{attachment}"


def _is_pulled_constraints(content: bytes) -> bool:
    return content.startswith(PULLED_CONSTRAINTS_HEADER.encode("utf-8"))


def _same(local: bytes, remote: bytes) -> bool:
    return local == remote or local.replace(b"\r\n", b"\n") == remote.replace(b"\r\n", b"\n")


def _join(detail: str, suffix: str) -> str:
    return f"{detail}; {suffix}" if detail else suffix


def _describe_limits(limits: tuple[int, int] | None) -> str:
    if limits is None:
        return "unreadable"
    return f"{limits[0]}ms/{limits[1]}MB"


def _is_plain_filename(name: str) -> bool:
    """The file name comes from Polygon — we don't let it lead the write outside the
    part's directory (`../x`, an absolute path)."""
    return bool(name) and name not in (".", "..") and Path(name).name == name and "\\" not in name
