---
paths:
  - "orchestrator/spec_critic.py"
  - "orchestrator/spec_from_draft.py"
  - "prompts/spec_critic/**"
  - "prompts/spec_from_draft/**"
  - "tests/test_spec_critic.py"
---

# Role of each generative step, item 8: `spec_critic`

> **Status: design, not implemented yet.** This file describes the target
> behavior for review; nothing in `orchestrator/` reads `prompts/spec_critic/`
> so far. When the implementation lands, drop this note and sync
> `docs/PROMPTS.md`, `.claude/rules/cli.md` (`init --draft`),
> `.claude/rules/models.md` and `README.md` in the same PR.

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
- `templates/tutorials/*.md` that define what later steps need from the spec
  (so "simpler" doesn't drop something a step requires).

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
finding by the marks in the unstripped spec (node lines via `yaml.compose`,
marks via `spec._REVIEW_MARK`):
- the finding's node, or an ancestor up to the nearest list item, carries a
  mark → a **model value**;
- otherwise (including a section that only partly contains marked values) →
  an **author value**;
- the path doesn't resolve → the finding goes to the report as unresolved.

Then:

| value | severity | action | outcome |
|---|---|---|---|
| author | any | any | report only — the author decides |
| any | `nit` | any | report only |
| model, `SUGGESTION` list item | `must`/`should` | `remove` | removed mechanically (text-level: the marked line and its deeper-indented continuation; PyYAML doesn't keep comments), with its entry in `notes`; if the block can't be cut unambiguously — goes to revision |
| model | `must`/`should` | other | revision |

## Revision round

The findings routed to revision go to `spec_from_draft` in its revision mode
(the current spec + the draft as usual) plus the block
`prompts/spec_critic/feedback.md.j2`. The generator may change only the
marked values named in the findings, and may keep a value, explaining why in
`notes` (`kind: uncertain`, `field` — the path): the critic is a reviewer, not
the author, and a forced "fix" can make the spec worse.

Mechanical guard after the revision: every unmarked value of the spec before
the round must be equal after it (by parsed YAML). Any difference — the whole
revision is rejected, the spec stays as before the round, and the report says
which values were touched.

At most `--critic-rounds` rounds (default 1, max 2). One round is
"critic → revision", without a second critique; with 2 — the critic runs again
on the revised spec, and the second round happens only if it has new
`must`/`should` findings on model values (the same `field` + `action` as in the
previous round doesn't count as new — that's oscillation, not progress).

## Where it runs

Inside `init --draft`, right after `spec_from_draft`, only when the model was
actually called (draft unchanged without `--force` — no critic either);
`--no-critic` turns it off. No cache, like `init --draft`. Calls the model —
the same restrictions on running without an explicit command as for
`llm generate`.

Model class: medium model, high effort — the judgment "is this needed" is
not as costly to get wrong as `spec_from_draft` itself: every outcome goes
through the generator or the author.

## Report

A "Critic" section in `llm_reports/spec_from_draft.md`, a row per finding:
field, author/model, severity, action, why, outcome (`removed`, `revised`,
`kept: <generator's reason>`, `for review`, `unresolved`, `revision rejected`).
Author-value findings first: those are what the author reads while checking
the marks.

## Later, not in the first version

- the critic on an author-written spec without `--draft` (report only);
- narrow critics with one lens each (test excess, statement wordiness, limits)
  instead of one general critic, and critics for other steps' output.
