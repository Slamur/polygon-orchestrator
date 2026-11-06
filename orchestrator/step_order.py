"""The step names of both pipelines in execution order — separately from the
steps themselves and without a single heavy import.

The CLI (`cli.py`) builds the parser (`choices` of `--step`) on every run,
including on every Tab press in shell completion, and mustn't pull in the
steps themselves, and with them pydantic and requests, for the sake of a list of names. That the
names match the real steps (`pipeline._STEP_MODULES`, `polygon.pipeline._POLYGON_STEPS`)
is checked by tests.
"""

from __future__ import annotations

# The order of the steps is fixed (CLAUDE.md, "Role of each generative step"):
# each next step may depend on the artifacts of the previous ones. `tests_plan`,
# `generators_draft` and `test_script_draft` — test preparation: the plan, then
# code from it. `checker_draft` depends on nothing (unlike its neighbors in the
# order), but is kept next to them — logically it's also part of the problem's test infrastructure,
# not of the solutions (CLAUDE.md, item 5). `interactor_draft` — there too and for
# the same reason (CLAUDE.md, item 6). `limits_from_constraints` — right
# after `constraints_pick`, from whose TL/ML it derives `limits.yaml`.
STEP_ORDER: list[str] = [
    "statement_draft",
    "constraints_pick",
    "limits_from_constraints",
    "tests_plan",
    "generators_draft",
    "test_script_draft",
    "checker_draft",
    "interactor_draft",
    "solutions_draft",
]

# Steps by a separate command, outside `STEP_ORDER`: `llm generate` doesn't run them,
# but `llm status|apply|accept` see them. The tutorial is written last and at the explicit
# request of the author (`llm tutorial`, CLAUDE.md, "Role of each generative
# step", item 7): otherwise any edit of the statement or a solution in a full run
# would spend a call to the strong model.
ON_DEMAND_STEPS: list[str] = ["tutorial_draft"]

LLM_STEPS: list[str] = [*STEP_ORDER, *ON_DEMAND_STEPS]

# Steps from `STEP_ORDER` without a model call and without prompts: the result is derived
# mechanically from the files of the previous steps.
MECHANICAL_STEPS: frozenset[str] = frozenset({"limits_from_constraints"})

# Steps whose prompts lie in a directory shared with other steps (`prompts/<dir>/`), not in
# `prompts/<step>/`; the user prompt tells the model which of them it is.
PROMPT_DIRS: dict[str, str] = {}


def prompt_dir_name(step_name: str) -> str:
    """The directory of the step's prompts inside `prompts/`."""
    return PROMPT_DIRS.get(step_name, step_name)


# The list is extended as steps are added — append new names (as
# a rule, at the end) and add an entry to `polygon.pipeline._POLYGON_STEPS`;
# nothing else needs changing if the new step is the same kind of `PolygonStep`,
# run via `steps.base.run_step`. `upload_attachments` (spec,
# `constraints.yaml`, draft) goes right after `create_problem`: the spec is the
# basis of the problem, and it must end up on Polygon even if one of the
# following steps fails. `upload_interactor` — after
# `set_constraints`: that one sets the problem's interactivity flag.
# `upload_test_script` — last: having saved the script, Polygon generates
# tests from it, and the step waits for the generation to end — everything else is already
# uploaded and committed by that time.
POLYGON_STEP_ORDER: list[str] = [
    "create_problem",
    "upload_attachments",
    "set_constraints",
    "upload_problem_lib",
    "upload_validator",
    "upload_checker",
    "upload_interactor",
    "upload_statement",
    "upload_generators",
    "upload_solutions",
    "upload_test_script",
]

# The lenses of the spec critic (`spec_critic.LENSES`), in their order: `choices` of
# `init --critic-lens` and `llm critic --lens`.
CRITIC_LENSES: list[str] = ["statement", "limits", "tests", "solutions"]
