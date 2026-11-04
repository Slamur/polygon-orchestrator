---
paths:
  - "orchestrator/spec_critic.py"
  - "orchestrator/spec_from_draft.py"
  - "prompts/spec_critic/**"
  - "prompts/spec_from_draft/**"
  - "tests/test_spec_critic.py"
  - "orchestrator/llm_report.py"
---

# Role of each generative step, item 8: `spec_critic`

Common to all steps (revision mode, conflicts, escalation) — in
`.claude/rules/steps.md`, models — in `.claude/rules/models.md`, `init` — in
`.claude/rules/cli.md`.

## Why

`spec_from_draft` fills the gaps of a draft and adds its own ideas
(`# PROPOSED, REVIEW ME`, `# SUGGESTION`). It tends to add too much: extra
test groups, generators, wrong approaches, wordy sketches. All of it is input
for every later step, so the excess multiplies. The critic is a second model
call that reads the finished spec as a fresh reader and asks "why is this
here? can it be simpler? why so much?". It only reduces: it never proposes
additions (completeness is the generator's job; a critic that may add makes
the spec grow with every round).

The critic is not one call but several narrow ones — one **lens** each
(`spec_critic.LENSES`). A general critic spreads its attention over the whole
spec and finds a little everywhere; a critic with one question ("is every test
idea needed?") reads its fields closely, and needs only the rules of the steps
that read them.

## Input: the spec only ("goldfish")

The critic sees no draft, no generator notes and no history of the problem —
only what the spec itself says. Whatever the spec can't justify on its own is
exactly what the critic should catch.
- `spec.yaml` after `spec_from_draft`, **with the review marks stripped**
  (the `# PROPOSED, REVIEW ME: ...` / `# SUGGESTION: ...` comments are removed
  mechanically): the critic doesn't know what came from the author, so it
  judges author and model values by the same standard, and the mark's own
  justification doesn't argue for the value;
- `docs/SPEC_FORMAT.md` and the sample specs from `examples/` — general
  calibration of what a normal amount of detail is (not about this problem);
- the `templates/tutorials/*.md` of its lens — what the steps that read the
  lens's fields need from them (so "simpler" doesn't drop something a step
  requires).

## Lenses

Each lens owns a set of spec fields; the sets don't overlap, so two lenses never
judge the same value. A lens sees the whole spec (the tests lens needs the
limits and the wrong approaches to judge the test ideas), but comments only on
its own fields; its focus — `prompts/spec_critic/lenses/<lens>.md`, its fields
are put into the prompt from the code.

| lens | fields | rules |
|---|---|---|
| `statement` — wordiness of the sketches | `title`, `statement_draft` | `statement.md` (+ `interactive.md`) |
| `limits` — extra variables, guarantees and limits | `constraints` without the two below | `limits.md`, `validator.md` |
| `tests` — test excess | `generation`, `constraints.test_groups_hint`, `constraints.test_groups_needed` | `tests.md`, `limits.md` |
| `solutions` — extra solutions, wrong approaches, a custom checker | `solutions`, `checker`, `interactor` | `solutions.md`, `checker.md` (+ `interactive.md`) |

`interactive.md` — only for an interactive problem. The other top-level fields
(`problem_id`, `language`, `references`) belong to no lens.

A finding outside its lens's fields isn't acted on: it goes to the report as
`outside the lens`. Within a round, a finding with the same `field` + `action`
as another lens's is dropped.

## Output

The usual response contract (`docs/PROMPTS.md`), one artifact
`critique.yaml`; status `confirmed` — no findings, `proposed` — there are
findings, `uncertain` is not used (an empty critique is a normal answer).
Each finding:

```yaml
- field: tests.groups[2]          # path in the spec, as in `notes`
  severity: must | should | nit
  action: remove | simplify | merge | question
  why: "..."                      # one or two sentences, concrete
  proposal: "..."                 # for simplify/merge: the replacement; optional otherwise
```

`merge` names the other field(s) in `why`. No `add` action.

## Routing — by the orchestrator, not the model

The critic doesn't know provenance; the orchestrator decides what to do with a
finding (of every lens, after all lenses of the round answered) by the marks in the unstripped spec (node lines via `yaml.compose`,
marks via `spec._REVIEW_MARK`):
- the finding's node or any of its ancestors carries a mark (on a mapping key's
  line or a list item's first line) → a **model value** (`classify`);
- otherwise (including a section that only partly contains marked values) →
  an **author value**;
- the path doesn't resolve → the finding goes to the report as unresolved;
- the path is outside the lens's fields → the report, `outside the lens`.

Then:

| value | severity | action | outcome |
|---|---|---|---|
| author | any | any | report only — the author decides |
| any | `nit` | any | report only |
| model, `SUGGESTION` list item | `must`/`should` | `remove` | removed mechanically (text-level: the marked line and its deeper-indented continuation; PyYAML doesn't keep comments), with its entry in `notes`; if the block can't be cut unambiguously — goes to revision |
| model | `must`/`should` | other | revision |

## Revision round

The findings of all lenses routed to revision go together, in one call, to `spec_from_draft` in its revision mode
(`spec_from_draft.revise_spec_by_findings`): the `findings` branch of
`prompts/spec_from_draft/user.md.j2` — the draft, the findings and the current
spec, the rules — in its `system.md`. The generator may change only the
marked values named in the findings, and may keep a value, explaining why in
`notes` (`kind: uncertain`, `field` — the path): the critic is a reviewer, not
the author, and a forced "fix" can make the spec worse.

Mechanical guard after the revision: every unmarked value of the spec before
the round must be equal after it (`author_values`: the parsed YAML without the
marked values; a value whose mark the revision dropped counts as the author's,
so it's a difference too). Any difference — the whole
revision is rejected, the spec stays as before the round, and the report says
which values were touched.

At most `--critic-rounds` rounds (default 1, max 2). One round is
"critics → revision", without a second critique; with 2 — the lenses that had
`must`/`should` findings on model values in the first round run again on the
revised spec (each gets its own previous findings), and the second revision
happens only if they have new ones (the same `field` + `action` as in the
previous round doesn't count as new — that's oscillation, not progress).

## Where it runs

Inside `init --draft`, right after `spec_from_draft`, only when the model was
actually called (draft unchanged without `--force` — no critic either);
`--no-critic` turns it off, `--critic-lens NAME` (repeatable) runs only the
named lenses, by default — all of them, one call each. No cache, like
`init --draft`. Calls the model — the same restrictions on running without an
explicit command as for `llm generate`. A failed call of one lens is a note of the
run (`spec_critic.<lens>`), the other lenses go on, the exit code is 1; if every
lens failed, the spec is kept as the generator wrote it.

## `llm critic`: report only

`orchestrator <problem> llm critic [--lens NAME ...]` runs the critics over the
spec as it is — written by hand, or edited since `init --draft`
(`spec_critic.review_spec`). The input is the same (the spec with the marks
stripped, the same documents per lens), one round, and nothing is acted on: there is no draft to revise against, so every
finding goes to `llm_reports/spec_critic.md` for the author. The findings are still
classified by the marks — the report lists the author's values first, then the values
still marked `PROPOSED` / `SUGGESTION`, then the unresolved paths. No cache: the
author runs it when they want a review. A failed call of a lens or an invalid
critique (notes of the run) — exit code 1.

Model class (the same for every lens): medium model, high effort — the judgment "is this needed" is
not as costly to get wrong as `spec_from_draft` itself: every outcome goes
through the generator or the author.

## Report

A "Critic" section in `llm_reports/spec_from_draft.md`, a row per finding:
field, lens, author/model, severity, action, why, outcome (`removed`, `revised`,
`kept: <generator's reason>`, `for review`, `unresolved`, `outside the lens`,
`revision rejected`).
Author-value findings first: those are what the author reads while checking
the marks. The spec before the critic — `authoring/spec.before-critic.yaml` in
the run's `.llm_backup/<time>/`, the critic's diff — in the report's "Diffs".
The outcome of a revised finding: `revised` — the value at its path changed,
`not changed` — the generator neither changed it nor gave a reason.

## Later, not in the first version

- critics for other steps' output (e.g. the wordiness of the generated
  statement), running the lenses in parallel.
