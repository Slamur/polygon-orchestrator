"""The critic of the spec written by `init --draft` (`.claude/rules/step-roles/8-spec_critic.md`).

A second model (`STEP_NAME` in `model_router.py`) reads the spec without the draft
and without the review marks — as a fresh reader — and proposes only to remove,
simplify or merge values (`prompts/spec_critic/`). It runs once per lens (`LENSES`):
each call sees the whole spec but judges only its lens's fields, with the rules of
the steps that read them; a finding outside them — the report only. What to do with
a finding is decided here, by the marks in the spec, not by the critic:
- a `gap` (something important is missing; only where a lens allows it — the
  statement examples) — the report only, whoever wrote the value: the critic
  doesn't make the spec grow;
- an author value (no `# PROPOSED, REVIEW ME` / `# SUGGESTION` mark on the value or
  its ancestors) or a `nit` — the report only;
- a `remove` of a `SUGGESTION` list item — removed mechanically, by editing lines
  (PyYAML doesn't keep comments) and checking the result by parsing;
- any other `must`/`should` on a proposed value — a revision round by the
  generator (`spec_from_draft.revise_spec_by_findings`). The revision may not
  change an unmarked value: if it does, it's rejected as a whole.

At most `rounds` rounds; the second one — only the lenses that had findings to act
on, and only if they have new ones. A failed call of a lens is a note of the run, the
other lenses go on.

`llm critic` reviews the spec as it is (`review_spec`): one call, every finding goes
to the report only, the spec isn't changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orchestrator import model_router
from orchestrator.llm_client import LLMCallError
from orchestrator.problem_paths import EXAMPLES_DIR
from orchestrator.spec import _REVIEW_MARK, SPEC_TEMPLATE, spec_validation_errors
from orchestrator.spec_from_draft import context_documents as spec_context_documents
from orchestrator.spec_from_draft import revise_spec_by_findings
from orchestrator.steps.base import (
    INTERACTIVE_TUTORIAL,
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_UNCERTAIN,
    TEMPLATES_DIR,
    Backup,
    read_system_prompt,
    render_user_prompt,
)

STEP_NAME = "spec_critic"
CRITIQUE_ARTIFACT = "critique.yaml"

# The spec before the critic's changes — in the run's `.llm_backup/<time>/`, next to
# the previous version of the spec.
BEFORE_CRITIC_FILENAME = "spec.before-critic.yaml"

DEFAULT_ROUNDS = 1
MAX_ROUNDS = 2

SEVERITIES = ("must", "should", "nit")
ACTIONS = ("remove", "simplify", "merge", "question", "gap")
# The only action that names something missing: never acted on, the report only.
GAP = "gap"

LENSES_DIRNAME = "lenses"
INTERACTIVE_LENS_TUTORIALS = ("statement", "solutions")

FieldPath = tuple[str | int, ...]


@dataclass(frozen=True)
class Lens:
    """One narrow critic: the spec fields it judges (`fields` without `excluded`; the
    lenses' fields don't overlap) and the rules of the steps that read them — "simpler"
    must not drop what a step needs. `gap_fields` — where it may name a `GAP`. Its
    focus — `prompts/spec_critic/lenses/<name>.md`."""

    name: str
    fields: tuple[str, ...]
    tutorials: tuple[str, ...]
    excluded: tuple[str, ...] = ()
    gap_fields: tuple[str, ...] = ()

    def covers(self, path: FieldPath, action: str = "") -> bool:
        def under(prefix: str) -> bool:
            parts = tuple(prefix.split("."))
            return path[: len(parts)] == parts

        if action == GAP:
            return any(under(f) for f in self.gap_fields)
        return any(under(f) for f in self.fields) and not any(under(f) for f in self.excluded)


_TEST_GROUP_FIELDS = ("constraints.test_groups_hint", "constraints.test_groups_needed")

# In the order of the spec's sections. `tutorials/interactive.md` is added to the
# `INTERACTIVE_LENS_TUTORIALS` lenses of an interactive problem.
LENSES: dict[str, Lens] = {
    lens.name: lens
    for lens in (
        Lens(
            "statement",
            ("title", "statement_draft"),
            ("tutorials/statement.md",),
            gap_fields=("statement_draft.sample_examples",),
        ),
        Lens(
            "limits",
            ("constraints",),
            ("tutorials/limits.md", "tutorials/validator.md"),
            excluded=_TEST_GROUP_FIELDS,
        ),
        Lens(
            "tests",
            ("generation", *_TEST_GROUP_FIELDS),
            ("tutorials/tests.md", "tutorials/limits.md"),
        ),
        Lens(
            "solutions",
            ("solutions", "checker", "interactor"),
            ("tutorials/solutions.md", "tutorials/checker.md"),
        ),
    )
}
LENS_NAMES = list(LENSES)

# Who wrote the value the finding is about.
AUTHOR = "author"
MODEL = "model"

# The outcomes of a finding (the report).
FOR_REVIEW = "for review"
REMOVED = "removed"
REVISED = "revised"
KEPT = "kept"
NOT_CHANGED = "not changed"
REJECTED = "revision rejected"
REPEATED = "repeated"
UNRESOLVED = "unresolved"
OUT_OF_LENS = "outside the lens"

_VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_UNCERTAIN}
_MARK_COMMENT = re.compile(r"\s*#\s*(?:PROPOSED, REVIEW ME|SUGGESTION)\b.*$")
_PATH_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")


@dataclass
class Finding:
    """One finding of the critic. `lens` — the lens that found it. `path` — the
    resolved path (sequence indices as `int`), `None` — `field` doesn't resolve in the
    spec. `owner` — `AUTHOR`/`MODEL`,
    `mark` — "SUGGESTION"/"PROPOSED" for a model value. `outcome` — what was done
    with it, `detail` — the generator's reason to keep it or the rejection reason."""

    field: str
    severity: str
    action: str
    why: str
    proposal: str = ""
    lens: str = ""
    round: int = 1
    path: FieldPath | None = None
    owner: str | None = None
    mark: str | None = None
    outcome: str = FOR_REVIEW
    detail: str = ""

    def to_yaml_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "field": self.field,
            "severity": self.severity,
            "action": self.action,
            "why": self.why,
        }
        if self.proposal:
            item["proposal"] = self.proposal
        return item


@dataclass
class CritiqueOutcome:
    """The critic's run over a spec. `spec_text` — the spec after all rounds,
    `before_text` — before the critic. `notes` — problems of the run itself
    (an invalid critique, a failed lens, a rejected revision) and the generator's notes
    from the revision rounds. `lenses` — the lenses that ran, `failed_lenses` — those
    whose call failed."""

    before_text: str
    spec_text: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    lenses: list[str] = field(default_factory=list)
    failed_lenses: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.spec_text != self.before_text

    def of_lens(self, name: str) -> CritiqueOutcome:
        """The findings and notes of one lens (`llm critic` writes a report per lens)."""
        return CritiqueOutcome(
            before_text=self.before_text,
            spec_text=self.spec_text,
            findings=[f for f in self.findings if f.lens == name],
            notes=[n for n in self.notes if n.get("lens") == name],
            rounds=self.rounds,
            lenses=[name] if name in self.lenses else [],
            failed_lenses=[name] if name in self.failed_lenses else [],
        )


def _note(field_name: str, kind: str, explanation: str) -> dict[str, Any]:
    return {"field": field_name, "kind": kind, "explanation": explanation}


def strip_review_marks(text: str) -> str:
    """The spec without the `init --draft` marks: the critic doesn't know who wrote what.
    A line that was only a mark is dropped."""
    lines = []
    for line in text.splitlines(keepends=True):
        if not _REVIEW_MARK.search(line):
            lines.append(line)
            continue
        stripped = _MARK_COMMENT.sub("", line.rstrip("\n"))
        if stripped.strip():
            lines.append(stripped + ("\n" if line.endswith("\n") else ""))
    return "".join(lines)


def format_path(path: FieldPath) -> str:
    out = ""
    for token in path:
        out += f"[{token}]" if isinstance(token, int) else ("." if out else "") + token
    return out


def _compose(text: str) -> yaml.Node | None:
    try:
        return yaml.compose(text)
    except yaml.YAMLError:
        return None


def _resolve(root: yaml.Node | None, field_name: str) -> tuple[FieldPath, list[int]] | None:
    """`field_name` (`tests.groups[2].name`; `tests.groups.2` is also accepted) in the
    composed spec: the normalized path and the lines of the path's steps (a mapping
    key's line, a sequence item's first line) — a mark on one of them marks the value."""
    tokens = [m.group(1) or m.group(2) for m in _PATH_TOKEN.finditer(field_name.strip())]
    if root is None or not tokens:
        return None
    node, path, lines = root, [], []
    for token in tokens:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == token:
                    path.append(token)
                    lines.append(key.start_mark.line)
                    node = value
                    break
            else:
                return None
        elif isinstance(node, yaml.SequenceNode) and token.isdigit():
            index = int(token)
            if index >= len(node.value):
                return None
            node = node.value[index]
            path.append(index)
            lines.append(node.start_mark.line)
        else:
            return None
    return tuple(path), lines


def _mark_kind(line: str) -> str | None:
    match = _REVIEW_MARK.search(line)
    if match is None:
        return None
    return "SUGGESTION" if "SUGGESTION" in match.group(0) else "PROPOSED"


def classify(finding: Finding, text: str) -> None:
    """Resolves the finding's path in the spec `text` (with marks) and sets its owner:
    the nearest mark on the path's steps — a model value, no mark — an author one."""
    resolved = _resolve(_compose(text), finding.field)
    if resolved is None:
        finding.path, finding.owner, finding.outcome = None, None, UNRESOLVED
        return
    finding.path, lines = resolved
    finding.field = format_path(finding.path)
    text_lines = text.splitlines()
    for line in reversed(lines):
        kind = _mark_kind(text_lines[line]) if line < len(text_lines) else None
        if kind is not None:
            finding.owner, finding.mark = MODEL, kind
            return
    finding.owner, finding.mark = AUTHOR, None


def _value_at(data: Any, path: FieldPath) -> Any:
    for token in path:
        data = data[token]
    return data


def _value_or_missing(text: str, path: FieldPath) -> Any:
    try:
        return _value_at(yaml.safe_load(text), path)
    except (yaml.YAMLError, KeyError, IndexError, TypeError):
        return _MISSING


_MISSING = object()


def remove_list_item(text: str, path: FieldPath) -> str | None:
    """The spec without the block-sequence item `path` — by lines, keeping the
    comments. `None` — the item can't be cut unambiguously (a flow sequence, the last
    item of a list, the result doesn't parse to the same data without the item)."""
    if not path or not isinstance(path[-1], int):
        return None
    root = _compose(text)
    resolved = _resolve(root, format_path(path))
    if resolved is None:
        return None
    lines = text.splitlines(keepends=True)
    start = resolved[1][-1]
    dash = lines[start]
    if not dash.lstrip().startswith("-"):
        return None
    indent = len(dash) - len(dash.lstrip())
    end = start + 1
    while end < len(lines) and (
        not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
    ):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    result = "".join(lines[:start] + lines[end:])

    expected = yaml.safe_load(text)
    parent = _value_at(expected, path[:-1])
    del parent[path[-1]]
    try:
        actual = yaml.safe_load(result)
    except yaml.YAMLError:
        return None
    return result if actual == expected and parent else None


def remap_path(path: FieldPath, removed: FieldPath) -> FieldPath | None:
    """`path` after removing the sequence item `removed`: the later items of the same
    sequence move up by one; `None` — `path` is inside the removed item."""
    parent, index = removed[:-1], removed[-1]
    depth = len(parent)
    if len(path) <= depth or path[:depth] != parent or not isinstance(path[depth], int):
        return path
    if path[depth] == index:
        return None
    if path[depth] > index:
        return (*parent, path[depth] - 1, *path[depth + 1 :])
    return path


def _unmarked(node: yaml.Node, lines: list[str]) -> Any:
    """The data of `node` without the marked values (a mark on a mapping key's line or
    a sequence item's first line drops that value) — what the revision may not change.
    Scalars — as (tag, text): the formatting of a value doesn't matter, its text does."""

    def marked(line: int) -> bool:
        return line < len(lines) and _REVIEW_MARK.search(lines[line]) is not None

    if isinstance(node, yaml.MappingNode):
        return {
            key.value: _unmarked(value, lines)
            for key, value in node.value
            if not marked(key.start_mark.line)
        }
    if isinstance(node, yaml.SequenceNode):
        return [_unmarked(item, lines) for item in node.value if not marked(item.start_mark.line)]
    return (node.tag, node.value)


def author_values(text: str) -> Any:
    """The spec's unmarked values (`_unmarked`); `None` — the spec doesn't parse."""
    root = _compose(text)
    return None if root is None else _unmarked(root, text.splitlines())


def critic_documents(
    spec_text: str,
    lens: Lens,
    *,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> dict[str, str]:
    """The documents of the `lens` critic: the spec format and the sample specs (from
    `spec_from_draft.context_documents`, without the template) and the rules of the
    steps that read the lens's fields."""
    templates_dir = Path(templates_dir)
    documents = spec_context_documents(templates_dir=templates_dir, examples_dir=examples_dir)
    del documents[SPEC_TEMPLATE]
    tutorials = list(lens.tutorials)
    try:
        data = yaml.safe_load(spec_text)
    except yaml.YAMLError:
        data = None
    if (
        isinstance(data, dict)
        and data.get("interactor")
        and lens.name in INTERACTIVE_LENS_TUTORIALS
    ):
        tutorials.append(INTERACTIVE_TUTORIAL)
    for name in tutorials:
        documents[name] = (templates_dir / name).read_text(encoding="utf-8")
    return documents


def parse_critique(text: str | None) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """The findings from `critique.yaml` and notes about the items that were skipped."""
    notes: list[dict[str, Any]] = []
    if text is None:
        return [], [_note(CRITIQUE_ARTIFACT, "uncertain", "the critic returned no critique")]
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], [_note(CRITIQUE_ARTIFACT, "uncertain", f"not valid YAML: {exc}")]
    if data is None:
        return [], []
    if not isinstance(data, list):
        return [], [_note(CRITIQUE_ARTIFACT, "uncertain", "not a list of findings")]
    items = []
    for number, item in enumerate(data):
        problem = None
        if not isinstance(item, dict):
            problem = "not a mapping"
        elif not isinstance(item.get("field"), str) or not item["field"].strip():
            problem = "no field"
        elif item.get("severity") not in SEVERITIES:
            problem = f"severity '{item.get('severity')}' is not one of {list(SEVERITIES)}"
        elif item.get("action") not in ACTIONS:
            problem = f"action '{item.get('action')}' is not one of {list(ACTIONS)}"
        if problem is not None:
            notes.append(
                _note(f"{CRITIQUE_ARTIFACT}[{number}]", "uncertain", f"skipped: {problem}")
            )
            continue
        items.append(
            {
                "field": item["field"].strip(),
                "severity": item["severity"],
                "action": item["action"],
                "why": str(item.get("why") or "").strip(),
                "proposal": str(item.get("proposal") or "").strip(),
            }
        )
    return items, notes


def _call_critic(
    spec_text: str,
    lens: Lens,
    previous: list[Finding],
    *,
    prompts_dir: Path,
    templates_dir: Path,
    examples_dir: Path,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    previous_findings = None
    if previous:
        previous_findings = yaml.safe_dump(
            [
                {
                    **f.to_yaml_item(),
                    "outcome": f.outcome + (f": {f.detail}" if f.detail else ""),
                }
                for f in previous
            ],
            allow_unicode=True,
            sort_keys=False,
        )
    user_prompt = render_user_prompt(
        STEP_NAME,
        {
            "lens": lens.name,
            "lens_focus": lens_focus(lens, prompts_dir=prompts_dir),
            "lens_fields": lens.fields,
            "lens_excluded": lens.excluded,
            "lens_gap_fields": lens.gap_fields,
            "spec": strip_review_marks(spec_text).rstrip("\n"),
            "previous_findings": previous_findings,
        },
        prompts_dir=prompts_dir,
    )
    response = model_router.call_model(
        STEP_NAME,
        read_system_prompt(STEP_NAME, prompts_dir=prompts_dir),
        user_prompt,
        context_documents=critic_documents(
            spec_text, lens, templates_dir=templates_dir, examples_dir=examples_dir
        ),
    )
    if response.status not in _VALID_STATUSES:
        raise ValueError(
            f"'{STEP_NAME}': the model returned an unknown status "
            f"'{response.status}' (expected one of {sorted(_VALID_STATUSES)})"
        )
    items, notes = parse_critique(response.artifacts.get(CRITIQUE_ARTIFACT))
    for filename in sorted(set(response.artifacts) - {CRITIQUE_ARTIFACT}):
        notes.append(_note(filename, "uncertain", "unexpected file in the critique, ignored"))
    for note in notes:
        note["explanation"] = f"({lens.name}) {note['explanation']}"
        note["lens"] = lens.name
    return items, notes


def lens_focus(lens: Lens, *, prompts_dir: Path = PROMPTS_DIR) -> str:
    path = Path(prompts_dir) / STEP_NAME / LENSES_DIRNAME / f"{lens.name}.md"
    return path.read_text(encoding="utf-8").rstrip("\n")


def _lenses(names: list[str] | None) -> list[Lens]:
    """The lenses to run, in the order of `LENSES`; `None` — all of them."""
    if names is None:
        return list(LENSES.values())
    unknown = sorted(set(names) - set(LENSES))
    if unknown:
        raise ValueError(f"unknown critic lens(es) {unknown}, expected some of {LENS_NAMES}")
    return [lens for name, lens in LENSES.items() if name in names]


def _critique_round(
    text: str,
    lenses: list[Lens],
    outcome: CritiqueOutcome,
    number: int,
    *,
    prompts_dir: Path,
    templates_dir: Path,
    examples_dir: Path,
) -> list[Finding]:
    """One call per lens over `text`, the findings classified by the marks. A finding
    outside its lens's fields — `OUT_OF_LENS`; one with the same field + action as
    another of this round is dropped, as in a previous round — `REPEATED`. A failed
    lens is a note; if every lens of the first round failed, the error is raised: there
    is no review at all."""
    seen = {(f.field, f.action) for f in outcome.findings}
    in_lens: list[Finding] = []
    outside: list[Finding] = []
    error: LLMCallError | None = None
    for lens in lenses:
        previous = [f for f in outcome.findings if f.lens == lens.name]
        try:
            items, notes = _call_critic(
                text,
                lens,
                previous,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
                examples_dir=examples_dir,
            )
        except LLMCallError as exc:
            error = exc
            outcome.failed_lenses.append(lens.name)
            note = _note(f"{STEP_NAME}.{lens.name}", "uncertain", f"failed: {exc}")
            outcome.notes.append({**note, "lens": lens.name})
            continue
        if lens.name not in outcome.lenses:
            outcome.lenses.append(lens.name)
        outcome.notes.extend(notes)
        for item in items:
            finding = Finding(**item, lens=lens.name, round=number)
            classify(finding, text)
            tokens = finding.path or _parse_note_path({"field": finding.field}) or ()
            (in_lens if lens.covers(tokens, finding.action) else outside).append(finding)
    if error is not None and number == 1 and len(outcome.failed_lenses) == len(lenses):
        raise error
    outside_ids = {id(f) for f in outside}
    findings = []
    round_keys: set[tuple[str, str]] = set()
    for finding in in_lens + outside:
        key = (finding.field, finding.action)
        if key in round_keys:
            continue
        round_keys.add(key)
        if key in seen:
            finding.outcome = REPEATED
        elif id(finding) in outside_ids and finding.outcome == FOR_REVIEW:
            finding.outcome = OUT_OF_LENS
        findings.append(finding)
    outcome.findings.extend(findings)
    return findings


def _remove_mechanically(
    text: str, removals: list[Finding], pending: list[Finding], notes: list[dict[str, Any]]
) -> tuple[str, list[Finding]]:
    """Removes the `SUGGESTION` items of `removals` (later items of a list first, so
    the indices of the others stay valid) and remaps the paths of `pending` and of the
    generator's `notes`. Returns the text and the findings that couldn't be cut — they
    go to the revision."""
    fallback = []
    for finding in sorted(removals, key=lambda f: f.path, reverse=True):
        result = remove_list_item(text, finding.path)
        if result is None:
            fallback.append(finding)
            continue
        text = result
        finding.outcome = REMOVED
        for other in pending + fallback:
            remapped = remap_path(other.path, finding.path)
            if remapped is None:
                other.outcome, other.path = REMOVED, None
                other.detail = f"inside {finding.field}"
            else:
                other.path = remapped
                other.field = format_path(remapped)
        kept_notes = []
        for note in notes:
            resolved = _parse_note_path(note)
            if resolved is None:
                kept_notes.append(note)
                continue
            remapped = remap_path(resolved, finding.path)
            if remapped is not None:
                note["field"] = format_path(remapped)
                kept_notes.append(note)
        notes[:] = kept_notes
    return text, fallback


def _parse_note_path(note: dict[str, Any]) -> FieldPath | None:
    name = note.get("field")
    if not isinstance(name, str):
        return None
    tokens = [m.group(1) or m.group(2) for m in _PATH_TOKEN.finditer(name)]
    path = tuple(int(t) if t.isdigit() else t for t in tokens)
    return path or None


def critique_spec(
    init,
    draft_text: str,
    spec_text: str,
    *,
    generator_notes: list[dict[str, Any]] | None = None,
    rounds: int = DEFAULT_ROUNDS,
    lenses: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> CritiqueOutcome:
    """Runs the critic over `spec_text` (the spec just written from `draft_text`)
    for at most `rounds` rounds (see the module docstring). `generator_notes` — the
    notes of `spec_from_draft`: their paths are remapped after mechanical removals,
    the notes of the removed values are dropped. `lenses` — the names of the lenses to
    run, `None` — all. Nothing is written to disk."""
    rounds = max(1, min(rounds, MAX_ROUNDS))
    outcome = CritiqueOutcome(before_text=spec_text, spec_text=spec_text)
    generator_notes = generator_notes if generator_notes is not None else []
    text = spec_text
    round_lenses = _lenses(lenses)
    for number in range(1, rounds + 1):
        findings = _critique_round(
            text,
            round_lenses,
            outcome,
            number,
            prompts_dir=prompts_dir,
            templates_dir=templates_dir,
            examples_dir=examples_dir,
        )
        outcome.rounds = number

        actionable = [
            f
            for f in findings
            if f.outcome == FOR_REVIEW
            and f.owner == MODEL
            and f.severity != "nit"
            and f.action != GAP
        ]
        if not actionable:
            break
        removals = [f for f in actionable if f.action == "remove" and f.mark == "SUGGESTION"]
        to_revise = [f for f in actionable if f not in removals]
        text, fallback = _remove_mechanically(text, removals, to_revise, generator_notes)
        to_revise = [f for f in to_revise if f.outcome == FOR_REVIEW] + fallback
        if to_revise:
            text = _revision_round(
                init,
                draft_text,
                text,
                to_revise,
                outcome,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
                examples_dir=examples_dir,
            )
        if number == rounds:
            break
        acted_on = {f.lens for f in actionable}
        round_lenses = [lens for lens in round_lenses if lens.name in acted_on]
    outcome.spec_text = text
    return outcome


def review_spec(
    spec_text: str,
    *,
    lenses: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    examples_dir: Path = EXAMPLES_DIR,
) -> CritiqueOutcome:
    """`llm critic`: one round of the `lenses` (`None` — all) over `spec_text`, the
    findings classified by the marks but not acted on — all of them are for the
    author's review. Nothing is written to disk."""
    outcome = CritiqueOutcome(before_text=spec_text, spec_text=spec_text, rounds=1)
    _critique_round(
        spec_text,
        _lenses(lenses),
        outcome,
        1,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        examples_dir=examples_dir,
    )
    return outcome


def _revision_round(
    init,
    draft_text: str,
    text: str,
    findings: list[Finding],
    outcome: CritiqueOutcome,
    *,
    prompts_dir: Path,
    templates_dir: Path,
    examples_dir: Path,
) -> str:
    """The generator's revision by `findings`; returns the spec after the round (the
    revised one or, if the revision touched an unmarked value, the one before)."""
    findings_yaml = yaml.safe_dump(
        [f.to_yaml_item() for f in findings], allow_unicode=True, sort_keys=False
    )
    revision = revise_spec_by_findings(
        init,
        draft_text,
        text,
        findings_yaml,
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        examples_dir=examples_dir,
    )
    fields = {f.field for f in findings}
    kept = {
        n.get("field"): str(n.get("explanation", "")).strip()
        for n in revision.notes
        if n.get("kind") == "uncertain" and n.get("field") in fields
    }
    outcome.notes.extend(
        {**n, "explanation": f"(revision by the critic) {n.get('explanation', '')}"}
        for n in revision.notes
        if n.get("field") not in kept
    )
    revised = revision.spec_text
    if revised is not None and revised != text:
        before, after = author_values(text), author_values(revised)
        if after is None or before != after:
            reason = (
                "the revised spec doesn't parse"
                if after is None
                else "the revision changed values without a mark"
            )
            for finding in findings:
                finding.outcome, finding.detail = REJECTED, reason
            outcome.notes.append(_note("spec.yaml", "uncertain", f"revision rejected: {reason}"))
            return text
    else:
        revised = text
    for finding in findings:
        if finding.field in kept:
            finding.outcome, finding.detail = KEPT, kept[finding.field]
        elif _value_or_missing(revised, finding.path) != _value_or_missing(text, finding.path):
            finding.outcome = REVISED
        else:
            finding.outcome = NOT_CHANGED
    return revised


def apply_critique(init, result, outcome: CritiqueOutcome) -> None:
    """Writes the spec after the critic over `init.path` and attaches `outcome` to the
    `spec_from_draft.SpecFromDraftResult`; the spec before the critic —
    `BEFORE_CRITIC_FILENAME` in the same backup directory."""
    result.critique = outcome
    if not outcome.changed:
        return
    backup = Backup(init.path.parent.parent)
    backup.directory = result.backup_dir
    backup.save_text(init.path.with_name(BEFORE_CRITIC_FILENAME), outcome.before_text)
    init.path.write_text(outcome.spec_text, encoding="utf-8")
    result.backup_dir = backup.directory
    result.validation_errors = spec_validation_errors(init.path, expected_id=init.problem_id)
