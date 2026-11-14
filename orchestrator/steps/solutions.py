"""Step 4 — drafts of the author solutions in stages, CLAUDE.md, "Role of each
generative step", item 4, and `.claude/rules/step-roles/4-solutions.md`.

The stages (`STAGES`), each a separate model call over the shared prompts
`prompts/solutions/` and checked by its local run (`solutions_check.Gate`) before
the next one:

- `solutions_main` — the main `ok` solution `ok_<language>_main_<author>` in the first
  of `MAIN_LANGUAGES` among `solutions.languages`: Main correct, whose output the other
  solutions are judged by;
- `solutions_ok` — the other `ok` solutions (the other languages, `alternative_approaches`),
  from the code of the main one;
- `solutions_slow` — `tl`, `tl_ok`, `ml` solutions; `solutions_wrong` — `wa`, `re`, `il`:
  from the code of the `ok` ones, the `known_wrong_approaches` items of the stage's
  verdicts (the item's verdict prefix is mandatory, `spec.parse_wrong_approach`).

A stage owns the solutions by their names (`Stage.owned`): `solutions_main` — Main correct
(`upload_solutions._choose_main`, a solution with the `main` description first),
`solutions_ok` — the other `ok` ones, the others — by the verdict. Revision mode shows the
model only the stage's files, and a response with a file of another stage isn't applied.
If its local run failed and nothing it read has changed since, the stage reruns
(`forced_rerun_reason`) and revises its solutions with the run's report: the report is
the context of the call, not its input, so after the revision the stage is a cache hit
again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import (
    MAIN_DESCRIPTION,
    _choose_main,
    _parse_solution_filename,
)
from orchestrator.references import reference_documents
from orchestrator.spec import (
    ProblemSpec,
    parse_wrong_approach,
    split_solution_stem,
)
from orchestrator.step_order import SOLUTION_STAGES
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    INTERACTIVE_TUTORIAL,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    artifact_filename,
    constraints_without_limits,
    directory_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
)
from orchestrator.steps.solutions_check import Gate

# The solutions directory inside a directory reference, see `extra_context_documents`.
SOLUTIONS_SUBDIR = "solutions"

# Solution skeletons in `templates/` — `<verdict>_<description>.<language>`
# (`ok.cpp`, `tl_ok_slowio.java`): input/output and the solution structure without
# an algorithm; the skeleton's language is its extension (as in solution names, it matches
# the language), see `extra_context_documents`.
SOLUTION_TEMPLATES_SUBDIR = "solutions"

# `solutions.languages` are free strings; we map common
# spellings to how the language is named in the template file names.
_LANGUAGE_ALIASES = {"c++": "cpp", "python": "py", "python3": "py"}

# The language of the main solution — the first of these among `solutions.languages`
# (the same priority as Main correct on Polygon), otherwise the first ordered one.
MAIN_LANGUAGES = ("cpp", "java", "py")

SLOW_VERDICTS = frozenset({"tl", "tle", "tl_ok", "ml", "mle"})
WRONG_VERDICTS = frozenset({"wa", "re", "il"})
# Languages in which a `tl_ok` solution with slow input/output makes sense.
_SLOWIO_LANGUAGES = frozenset({"java", "py"})

_SOLUTIONS_SECTION_SKIP = (
    "spec has no 'solutions' section — solution drafts were not requested by the author"
)


def normalized_languages(spec: ProblemSpec) -> list[str]:
    assert spec.solutions is not None
    languages = []
    for lang in spec.solutions.languages:
        name = _LANGUAGE_ALIASES.get(lang.lower(), lang.lower())
        if name not in languages:
            languages.append(name)
    return languages


def main_language(spec: ProblemSpec) -> str:
    languages = normalized_languages(spec)
    return next((lang for lang in MAIN_LANGUAGES if lang in languages), languages[0])


def _verdict(name: str) -> str | None:
    parts = split_solution_stem(Path(name).stem)
    return parts[0].lower() if len(parts) >= 3 else None


def _description(name: str) -> str | None:
    parts = split_solution_stem(Path(name).stem)
    return parts[2].lower() if len(parts) >= 3 else None


def solution_files(problem_dir: Path) -> dict[str, Path]:
    return directory_artifacts(Path(problem_dir) / SOLUTIONS_SUBDIR)


def main_solution(files: dict[str, Path]) -> str | None:
    """Main correct among `files`, as Polygon chooses it; `None` — no `ok` solution."""
    parsed = []
    for path in files.values():
        try:
            parsed.append(_parse_solution_filename(path))
        except PolygonStepError:
            continue
    try:
        return _choose_main(parsed)
    except PolygonStepError:
        return None


@dataclass(frozen=True)
class Stage:
    """A solution stage: `key` — `main`, `ok`, `slow` or `wrong`."""

    key: str

    @property
    def STEP_NAME(self) -> str:  # noqa: N802 — the same interface as the step modules
        return f"solutions_{self.key}"

    @property
    def INPUTS_KEY(self) -> str:  # noqa: N802
        return f"{self.STEP_NAME}: inputs"

    @property
    def gate(self) -> Gate:
        return GATES[SOLUTION_STAGES[self.STEP_NAME]]

    # --- what the stage owns ------------------------------------------------------

    def owned(self, problem_dir: Path) -> dict[str, Path]:
        """The stage's solutions in the problem directory."""
        files = solution_files(problem_dir)
        main = main_solution(files)
        return {name: path for name, path in files.items() if self._owns(name, main)}

    def _owns(self, name: str, main: str | None) -> bool:
        verdict = _verdict(name)
        if self.key == "main":
            return name == main
        if self.key == "ok":
            return verdict == "ok" and name != main
        return verdict in (SLOW_VERDICTS if self.key == "slow" else WRONG_VERDICTS)

    def _may_write(self, name: str, current: set[str]) -> bool:
        """Whether the model may return `name` in this stage: a file of the stage's own."""
        if name in current:
            return True
        verdict, description = _verdict(name), _description(name)
        if self.key == "main":
            return verdict == "ok" and description == MAIN_DESCRIPTION
        if self.key == "ok":
            return verdict == "ok" and description != MAIN_DESCRIPTION
        return verdict in (SLOW_VERDICTS if self.key == "slow" else WRONG_VERDICTS)

    def judged(self, problem_dir: Path) -> list[str]:
        """The solutions the stage's local run judges: the stage's own; `solutions_ok` —
        together with Main correct."""
        names = list(self.owned(problem_dir))
        if self.key == "ok":
            main = main_solution(solution_files(problem_dir))
            names += [main] if main is not None else []
        return names

    # --- whether the stage is needed ----------------------------------------------

    def skip_detail(self, spec: ProblemSpec, **_: Any) -> str | None:
        """Why the stage isn't needed for this problem; `None` — it is."""
        solutions = spec.solutions
        if solutions is None:
            return _SOLUTIONS_SECTION_SKIP
        if self.key == "main":
            return None
        if self.key == "ok":
            if len(normalized_languages(spec)) > 1 or solutions.alternative_approaches:
                return None
            return (
                "one language and no alternative_approaches — the main solution is the only ok one"
            )
        verdicts = SLOW_VERDICTS if self.key == "slow" else WRONG_VERDICTS
        if set(solutions.wanted_verdicts) & verdicts or self._approaches(spec):
            return None
        if self.key == "slow" and _SLOWIO_LANGUAGES & set(normalized_languages(spec)):
            # A `tl_ok` solution with slow input/output, if the input/output is large.
            return None
        return (
            f"no {'/'.join(sorted(verdicts))} in solutions.wanted_verdicts and "
            "known_wrong_approaches — no such solutions were requested"
        )

    def _approaches(self, spec: ProblemSpec) -> list[str]:
        """The `known_wrong_approaches` items of the stage's verdicts."""
        assert spec.solutions is not None
        if self.key in ("main", "ok"):
            return []
        verdicts = SLOW_VERDICTS if self.key == "slow" else WRONG_VERDICTS
        items = spec.solutions.known_wrong_approaches or []
        return [
            item
            for item in items
            if (parsed := parse_wrong_approach(item)) is not None and parsed.verdict in verdicts
        ]

    # --- the input -----------------------------------------------------------------

    def _section(self, spec: ProblemSpec) -> dict[str, Any]:
        """The spec sections for rendering the prompt AND for the cache hash input — a single
        assembly point (CLAUDE.md, "Caching by spec hash"): the stage's part of
        `solutions`, `constraints.intended_complexity` and the statement from
        `statement_draft` in its input/output part (`base.statement_io_section`). Fields
        the stage doesn't use aren't taken: e.g. editing `known_wrong_approaches`
        doesn't invalidate the `ok` solutions."""
        assert spec.solutions is not None
        solutions: dict[str, Any] = {"algorithm_hint": spec.solutions.algorithm_hint}
        if self.key == "main":
            solutions["language"] = main_language(spec)
        else:
            solutions["languages"] = spec.solutions.languages
        if self.key == "ok":
            solutions["main_language"] = main_language(spec)
            solutions["alternative_approaches"] = spec.solutions.alternative_approaches or []
        if self.key in ("slow", "wrong"):
            verdicts = SLOW_VERDICTS if self.key == "slow" else WRONG_VERDICTS
            solutions["wanted_verdicts"] = [
                v for v in spec.solutions.wanted_verdicts if v in verdicts
            ]
            solutions["known_wrong_approaches"] = self._approaches(spec)
        return {
            "solutions": solutions,
            "constraints": {"intended_complexity": spec.constraints.intended_complexity},
            "statement": statement_io_section(spec, include_output_and_legend=True),
        }

    def load_inputs(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Any]:
        """The solutions of the earlier stages the stage builds on (`ok` — the main one,
        `slow`/`wrong` — all `ok` ones) and the failed report of its local run, if it's
        still current. `ValueError` — there's no main solution to build on."""
        problem_dir = Path(problems_dir) / problem_id
        files = solution_files(problem_dir)
        references: dict[str, Path] = {}
        if self.key != "main":
            main = main_solution(files)
            if main is None:
                raise ValueError(
                    f"Step '{self.STEP_NAME}' for '{problem_id}': no main ok solution in "
                    "solutions/ to build on — run the 'solutions_main' step first"
                )
            references = {main: files[main]}
            if self.key in ("slow", "wrong"):
                references.update(STAGES["ok"].owned(problem_dir))
        report = self.gate.failed_report(
            problem_id, spec, list(self.owned(problem_dir)), problems_dir=problems_dir
        )
        return {
            "references": {
                name: path.read_text(encoding="utf-8") for name, path in sorted(references.items())
            },
            "local_run": report,
        }

    def _inputs(self, problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict:
        if not upstream_artifacts or self.INPUTS_KEY not in upstream_artifacts:
            raise ValueError(
                f"Step '{self.STEP_NAME}' for '{problem_id}': the solutions it builds on "
                "aren't loaded"
            )
        return upstream_artifacts[self.INPUTS_KEY]

    def hash_inputs(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        """What goes into the step's input hash: `(spec section, upstream artifacts)` —
        `constraints.yaml` without TL/ML (tuning the TL doesn't invalidate the solutions)
        and the code of each solution the stage builds on. The report of the local run
        isn't part of it (see `forced_rerun_reason`)."""
        if spec.solutions is None:
            raise ValueError(
                f"Step '{self.STEP_NAME}' for '{problem_id}': {_SOLUTIONS_SECTION_SKIP}"
            )
        constraints_yaml = constraints_without_limits(
            require_constraints_yaml(self.STEP_NAME, problem_id, upstream_artifacts)
        )
        references = self._inputs(problem_id, upstream_artifacts)["references"]
        upstream = {CONSTRAINTS_ARTIFACT: constraints_yaml}
        upstream.update({f"{SOLUTIONS_SUBDIR}/{name}": code for name, code in references.items()})
        return self._section(spec), upstream

    def compute_input_hash(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> str:
        return _cache_compute_input_hash(
            self.STEP_NAME, *self.hash_inputs(problem_id, spec, upstream_artifacts)
        )

    def forced_rerun_reason(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
    ) -> str | None:
        """Why the stage reruns even with a valid cache: its local run failed on its
        solutions, and nothing the run read has changed since."""
        report = self._inputs(problem_id, upstream_artifacts)["local_run"]
        if report is None:
            return None
        names = ", ".join(failure["name"] for failure in report["failures"])
        return (
            f"the local run '{self.gate.step_name}' failed on {names} — revising with its "
            f"report ({report['report']})"
        )

    # --- files ---------------------------------------------------------------------

    def primary_artifact_path(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> Path:
        return Path(problems_dir) / problem_id / SOLUTIONS_SUBDIR

    def current_artifacts(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> dict[str, Path]:
        """The stage's current files on disk — for revision mode (`base.StepRevision`)."""
        return self.owned(Path(problems_dir) / problem_id)

    def artifact_present(
        self, problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
    ) -> bool:
        """The main solution is in place; for the other stages — any of their solutions,
        and once the stage has run, also none: a stage may legitimately write nothing
        (e.g. no slow input/output is needed), and that mustn't call the model on
        every run."""
        if self.owned(Path(problems_dir) / problem_id):
            return True
        if self.key == "main":
            return False
        from orchestrator.cache import load_cache_entry

        return load_cache_entry(problem_id, self.STEP_NAME, problems_dir=problems_dir) is not None

    def extra_context_documents(
        self, spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
    ) -> list[str] | None:
        return extra_context_documents(self.STEP_NAME, spec, templates_dir=templates_dir)

    # --- the run -------------------------------------------------------------------

    def run_step(
        self,
        problem_id: str,
        spec: ProblemSpec,
        upstream_artifacts: dict[str, Any] | None = None,
        *,
        revision: StepRevision | None = None,
        saved_response: ModelResponse | None = None,
        prompts_dir: Path = PROMPTS_DIR,
        problems_dir: Path = PROBLEMS_DIR,
        templates_dir: Path = TEMPLATES_DIR,
    ) -> StepResult:
        """Runs the stage for `problem_id`: the solutions of the stage go to
        `problems/<problem_id>/solutions/`. The algorithm — ONLY from
        `solutions.algorithm_hint` (and `alternative_approaches` for the additional
        `ok` solutions); if it's empty or too vague — `status: uncertain`
        (`StepUncertainError`), nothing is written."""
        section, upstream = self.hash_inputs(problem_id, spec, upstream_artifacts)
        inputs = self._inputs(problem_id, upstream_artifacts)
        context = {
            "problem_id": spec.problem_id,
            "stage": self.key,
            **section,
            "constraints_yaml": upstream[CONSTRAINTS_ARTIFACT],
            "references": inputs["references"],
            "local_run": inputs["local_run"],
        }
        current = set(self.current_artifacts(problem_id, spec, problems_dir=problems_dir))

        def resolve_artifact_path(filename: str) -> Path:
            return (
                Path(problems_dir)
                / problem_id
                / SOLUTIONS_SUBDIR
                / artifact_filename(filename, SOLUTIONS_SUBDIR)
            )

        def validate_artifacts(artifacts: dict[str, str]) -> None:
            names = []
            for filename in artifacts:
                name = resolve_artifact_path(filename).name
                if not self._may_write(name, current):
                    raise ValueError(
                        f"Step '{self.STEP_NAME}' returned '{name}', which is not a solution "
                        f"of this stage ({_STAGE_FILES[self.key]})"
                    )
                names.append(name)
            if self.key == "main" and len(set(names) | current) > 1:
                raise ValueError(
                    f"Step '{self.STEP_NAME}' must write exactly one main solution, got "
                    f"{sorted(set(names) | current)}"
                )

        return run_generative_step(
            step_name=self.STEP_NAME,
            problem_id=problem_id,
            context=context,
            input_hash=self.compute_input_hash(problem_id, spec, upstream_artifacts),
            input_values=compute_input_values(section, upstream),
            revision=revision,
            saved_response=saved_response,
            resolve_artifact_path=resolve_artifact_path,
            validate_artifacts=validate_artifacts,
            # The files are independent: solutions are applied one by one.
            partial_apply=True,
            prompts_dir=prompts_dir,
            problems_dir=problems_dir,
            templates_dir=templates_dir,
            extra_context_documents=self.extra_context_documents(spec, templates_dir=templates_dir),
        )


_STAGE_FILES = {
    "main": f"ok_<language>_{MAIN_DESCRIPTION}_<author>",
    "ok": f"ok_* without the '{MAIN_DESCRIPTION}' description",
    "slow": "/".join(sorted(SLOW_VERDICTS)),
    "wrong": "/".join(sorted(WRONG_VERDICTS)),
}

STAGES: dict[str, Stage] = {key: Stage(key) for key in ("main", "ok", "slow", "wrong")}
GATES: dict[str, Gate] = {
    gate: Gate(
        step_name=gate,
        stage_step=stage,
        judged=STAGES[stage.removeprefix("solutions_")].judged,
        skip=STAGES[stage.removeprefix("solutions_")].skip_detail,
    )
    for stage, gate in SOLUTION_STAGES.items()
}

# The stages and their local runs in the order of `step_order.STEP_ORDER`.
SOLUTION_STEPS: dict[str, Stage | Gate] = {
    name: step
    for stage, gate in SOLUTION_STAGES.items()
    for name, step in ((stage, STAGES[stage.removeprefix("solutions_")]), (gate, GATES[gate]))
}


def extra_context_documents(
    step_name: str, spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Samples for a particular call — the files the spec's references
    give this step (the shared `references` and `solutions.references`, see
    `orchestrator/references.py`). From the `solutions/` of a directory reference only
    the solutions in the languages ordered in `solutions.languages` are taken — a sample of
    the solution structure and input/output for each language (see
    `prompts/solutions/system.md`). A solution's language is the second component
    of the file name by the `<verdict>_<language>_...` scheme (`ok_cpp_prefs_draft.cpp`
    -> `cpp`); solutions in languages not ordered aren't attached, so as not to
    pull someone else's input/output style into the context.

    For an ordered language for which the references gave no
    `ok` solution, all solution skeletons of this
    language from `templates/solutions/*.<language>` (if any) are attached instead of a sample: the way
    the author wants to see input/output in this language is set even without
    references. The skeletons go before the reference files. For an interactive problem
    the `INTERACTIVE_TUTORIAL` rules are attached first (as for
    `statement_draft`). If there's no `solutions` section — `None`: the step
    doesn't run anyway.
    """
    if spec.solutions is None:
        return None

    languages = set(normalized_languages(spec))

    def keep(rel_path: str) -> bool:
        if not rel_path.startswith(f"{SOLUTIONS_SUBDIR}/"):
            return True
        return _solution_parts(Path(rel_path))[1] in languages

    rules = [str(Path(templates_dir) / INTERACTIVE_TUTORIAL)] if spec.interactive else []
    references = reference_documents(step_name, spec, keep=keep) or []
    covered = {
        language
        for verdict, language in map(_solution_parts, map(Path, references))
        if verdict == "ok"
    }
    templates = [
        str(path)
        for language in sorted(languages - covered)
        for path in sorted((Path(templates_dir) / SOLUTION_TEMPLATES_SUBDIR).glob(f"*.{language}"))
    ]
    return [*rules, *templates, *references] or None


def _solution_parts(path: Path) -> tuple[str | None, str | None]:
    """(verdict, language) by a solution's name `<verdict>_<language>_...`
    (`tl_ok_java_slowio_draft.java` -> `("tl_ok", "java")`); `None` where
    the name doesn't follow the scheme."""
    parts = split_solution_stem(path.stem)
    return (parts[0] if parts else None), (parts[1] if len(parts) >= 2 else None)
