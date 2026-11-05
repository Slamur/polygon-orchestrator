"""Human-readable reports on the model's work: `llm_reports/<step>.md` in the
problem directory.

A report is the step's current state, not a history: the step's file is rewritten
only when the step actually worked in the run (called the model or applied a
saved response, `StepOutcome.ran`); a step skipped by the cache keeps
the report of its last run. The run history is in `llm_generation.log`.

The report starts with what requires the author's action (`uncertain`, what wasn't
applied, compile errors, `proposed`), then the model's ideas
(`suggestion`), what it changed, the step's files and the diffs of changed files
against the previous versions from `.llm_backup/`. `init --draft` likewise writes
`llm_reports/spec_from_draft.md` (with the critic's findings and what was done
with them), `llm critic` — `llm_reports/spec_critic/<lens>.md`, `llm accept` — `llm_reports/accept.md` (what was accepted and what
changed in the input of each step).
"""

from __future__ import annotations

import datetime
import difflib
import re
from pathlib import Path
from typing import Any

from orchestrator.steps.base import CONFLICTS_REPORT

REPORTS_DIRNAME = "llm_reports"
SPEC_FROM_DRAFT_REPORT = "spec_from_draft"
SPEC_CRITIC_REPORT = "spec_critic"
ACCEPT_REPORT = "accept"

# The order and titles of the note groups; notes of other `kind`s — at the end, in
# `_OTHER_NOTES_TITLE`.
_NOTE_SECTIONS = (
    ("uncertain", "Uncertain — the spec needs fixing"),
    ("proposed", "Proposed — review"),
    ("suggestion", "Suggestions beyond the spec"),
    ("uncovered", "Not covered by the tests plan — review"),
    ("plan_mismatch", "Differs from the tests plan — review"),
    ("changed", "Changed"),
)
_OTHER_NOTES_TITLE = "Other notes"


def report_path(problem_dir: Path, name: str) -> Path:
    return Path(problem_dir) / REPORTS_DIRNAME / f"{name}.md"


def note_counts(notes: list[dict[str, Any]]) -> str:
    """`2 proposed, 1 changed` — how many notes of which kind, in the order of
    `_NOTE_SECTIONS`; empty if there are no notes."""
    counts: dict[str, int] = {}
    for note in notes:
        kind = str(note.get("kind", "?"))
        counts[kind] = counts.get(kind, 0) + 1
    order = [kind for kind, _ in _NOTE_SECTIONS] + sorted(
        set(counts) - {kind for kind, _ in _NOTE_SECTIONS}
    )
    return ", ".join(f"{counts[kind]} {kind}" for kind in order if kind in counts)


def write_step_report(
    problem_dir: Path, outcome: Any, *, status_label: str, command_line: str
) -> Path:
    """Writes `llm_reports/<step>.md` from the `pipeline.StepOutcome` of a step that worked;
    returns the report's path."""
    problem_dir = Path(problem_dir)
    lines = _header(outcome.step_name, status_label, command_line)
    if outcome.detail:
        lines += [f"**Details:** {outcome.detail}", ""]

    if outcome.conflicts:
        lines += ["## Not applied", ""]
        lines += [f"- `{name}` — {reason}" for name, reason in outcome.conflicts]
        if outcome.conflicts_dir is not None:
            lines.append(
                "- model versions and the report: "
                + _link(problem_dir, outcome.conflicts_dir / CONFLICTS_REPORT)
            )
        lines.append("")
    if outcome.compile_errors:
        lines += ["## Compile errors", ""]
        for name, stderr in outcome.compile_errors.items():
            lines += [f"### `{name}`", "", *_fenced(stderr.strip(), "text"), ""]
    if outcome.charset_errors:
        lines += ["## Non-ASCII characters in sources", ""]
        for name, message in outcome.charset_errors.items():
            lines += [f"### `{name}`", "", *_fenced(message, "text"), ""]

    lines += _notes_sections(outcome.notes)
    lines += _files_section(problem_dir, outcome)
    lines += _diffs_section(problem_dir, outcome)
    return _write(report_path(problem_dir, outcome.step_name), lines)


def write_spec_from_draft_report(
    problem_dir: Path,
    result: Any,
    *,
    status_label: str,
    command_line: str,
    draft_path: Path,
) -> Path:
    """Writes `llm_reports/spec_from_draft.md` from `SpecFromDraftResult`."""
    problem_dir = Path(problem_dir)
    lines = _header(SPEC_FROM_DRAFT_REPORT, status_label, command_line)
    lines += [f"**Draft:** `{draft_path}`", ""]
    if result.path is None:
        lines += ["**No spec written** — see the notes below, fix the draft and run again.", ""]
    elif result.unchanged:
        lines += [f"**Spec:** {_link(problem_dir, result.path)} — no changes needed", ""]
    else:
        lines += [
            f"**Spec:** {_link(problem_dir, result.path)} — review the "
            "`# PROPOSED, REVIEW ME` / `# SUGGESTION` marks in it",
            "",
        ]
    if result.validation_errors:
        lines += ["## The spec does not pass validation yet", ""]
        lines += [f"- {error}" for error in result.validation_errors]
        lines.append("")
    critique = getattr(result, "critique", None)
    if critique is not None:
        lines += _critique_section(critique)
        lines += _notes_sections(critique.notes, title_prefix="Critic run — ")
    lines += _notes_sections(result.notes)
    diffs: list[str] = []
    before = (
        None if result.path is None else _backup_of(problem_dir, result.backup_dir, result.path)
    )
    if before is not None:
        diffs += _diff_block(problem_dir, before, result.path, result.path)
    if critique is not None and critique.changed and result.backup_dir is not None:
        from orchestrator.spec_critic import BEFORE_CRITIC_FILENAME

        before_critic = _backup_of(
            problem_dir, result.backup_dir, result.path.with_name(BEFORE_CRITIC_FILENAME)
        )
        if before_critic is not None:
            diffs += ["The critic's changes:", ""]
            diffs += _diff_block(problem_dir, before_critic, result.path, result.path)
    lines += ["## Diffs", "", *diffs] if diffs else []
    if result.backup_dir is not None:
        lines += [f"Previous versions: `{_relative(problem_dir, result.backup_dir)}/`", ""]
    return _write(report_path(problem_dir, SPEC_FROM_DRAFT_REPORT), lines)


def write_spec_critic_reports(
    problem_dir: Path, outcome: Any, *, spec_path: Path, command_line: str
) -> list[Path]:
    """Writes `llm_reports/spec_critic/<lens>.md` for every lens of the
    `spec_critic.CritiqueOutcome` of `llm critic` that ran or failed: the findings
    only, the spec isn't changed. The reports of the other lenses are kept."""
    from orchestrator.spec_critic import LENS_NAMES

    ran = set(outcome.lenses) | set(outcome.failed_lenses)
    return [
        _write_spec_critic_report(
            problem_dir,
            outcome.of_lens(name),
            name,
            spec_path=spec_path,
            command_line=command_line,
        )
        for name in LENS_NAMES
        if name in ran
    ]


def _write_spec_critic_report(
    problem_dir: Path, outcome: Any, lens: str, *, spec_path: Path, command_line: str
) -> Path:
    problem_dir = Path(problem_dir)
    name = f"{SPEC_CRITIC_REPORT}/{lens}"
    if outcome.failed_lenses:
        status_label = "failed"
    elif outcome.findings:
        status_label = f"{len(outcome.findings)} finding(s)"
    else:
        status_label = "no findings"
    lines = _header(name, status_label, command_line)
    lines += [
        f"**Spec:** {_link(problem_dir, spec_path, depth=2)} — not changed, the findings are "
        "for you",
        "",
    ]
    lines += _critique_section(outcome, review_only=True)
    lines += _notes_sections(outcome.notes, title_prefix="Critic run — ")
    return _write(report_path(problem_dir, name), lines)


def write_accept_report(problem_dir: Path, statuses: list[Any], *, command_line: str) -> Path:
    """Writes `llm_reports/accept.md` from the `pipeline.StepStatus` of the steps that
    `llm accept` accepted or didn't accept: the full `detail` of each — the console
    gets the short `brief`."""
    lines = [f"# {ACCEPT_REPORT}", "", f"`orchestrator {command_line}` · {_stamp()}", ""]
    for status in statuses:
        lines += [f"## {status.step_name}: {status.state}", ""]
        if status.detail:
            lines += [status.detail, ""]
    return _write(report_path(Path(problem_dir), ACCEPT_REPORT), lines)


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _header(name: str, status_label: str, command_line: str) -> list[str]:
    return [f"# {name}: {status_label}", "", f"`orchestrator {command_line}` · {_stamp()}", ""]


def _critique_section(critique: Any, *, review_only: bool = False) -> list[str]:
    """The critic's findings (`spec_critic.CritiqueOutcome`): first those on the
    author's values — what the author decides, then those on the model's values with
    what was done with them, then the rest (nits, repeats, unresolved paths).
    `review_only` (`llm critic`) — nothing was done with any finding: the model's
    values are the ones still marked in the spec."""
    from orchestrator.spec_critic import AUTHOR, FOR_REVIEW, MODEL

    rounds = f", {critique.rounds} round(s)" if critique.rounds > 1 else ""
    lenses = f" · lenses: {', '.join(critique.lenses)}" if critique.lenses else ""
    failed = f" · failed: {', '.join(critique.failed_lenses)}" if critique.failed_lenses else ""
    lines = [f"## Critic: {len(critique.findings)} finding(s){rounds}{lenses}{failed}", ""]
    if not critique.findings:
        return lines + ["No findings.", ""]
    if review_only:
        model_title = "Values still marked PROPOSED / SUGGESTION — for your review"
        model_values = [f for f in critique.findings if f.owner == MODEL]
    else:
        model_title = "Proposed values — sent back to the model"
        model_values = [
            f for f in critique.findings if f.owner == MODEL and f.outcome != FOR_REVIEW
        ]
    groups = (
        (
            "Your values — for your review",
            [f for f in critique.findings if f.owner == AUTHOR and f.outcome == FOR_REVIEW],
        ),
        (model_title, model_values),
    )
    listed = {id(f) for _, group in groups for f in group}
    other = [f for f in critique.findings if id(f) not in listed]
    for title, group in (
        *groups,
        ("Other — nits, repeats, unresolved paths, outside the lens", other),
    ):
        if not group:
            continue
        lines += [f"### {title} ({len(group)})", ""]
        for finding in group:
            why = finding.why.replace("\n", "\n  ")
            lines.append(
                f"- **{finding.field}** · {finding.lens} · {finding.severity} · "
                f"{finding.action} — {why}"
            )
            if finding.proposal:
                proposal = finding.proposal.replace("\n", "\n  ")
                lines.append(f"  Proposal: {proposal}")
            if finding.outcome != FOR_REVIEW:
                detail = f": {finding.detail}" if finding.detail else ""
                lines.append(f"  → **{finding.outcome}**{detail}")
            lines.append("")
    return lines


def _notes_sections(notes: list[dict[str, Any]], *, title_prefix: str = "") -> list[str]:
    known = {kind for kind, _ in _NOTE_SECTIONS}
    sections = [
        (title, [n for n in notes if n.get("kind") == kind]) for kind, title in _NOTE_SECTIONS
    ]
    sections.append((_OTHER_NOTES_TITLE, [n for n in notes if n.get("kind") not in known]))
    lines: list[str] = []
    for title, group in sections:
        if not group:
            continue
        lines += [f"## {title_prefix}{title} ({len(group)})", ""]
        for note in group:
            explanation = str(note.get("explanation", "")).strip().replace("\n", "\n  ")
            prefix = "" if title != _OTHER_NOTES_TITLE else f"[{note.get('kind', '?')}] "
            lines += [f"- {prefix}**{note.get('field', '?')}** — {explanation}", ""]
    return lines


def _files_section(problem_dir: Path, outcome: Any) -> list[str]:
    created = set(outcome.created)
    changed, new = [], []
    for path in outcome.artifact_paths.values():
        if path in created:
            new.append(path)
        elif _backup_of(problem_dir, outcome.backup_dir, path) is not None:
            changed.append(path)
    lines: list[str] = []
    lines += [f"- changed: {_link(problem_dir, path)}" for path in changed]
    lines += [f"- new: {_link(problem_dir, path)}" for path in new]
    lines += [
        f"- renamed: `{_relative(problem_dir, old)}` -> {_link(problem_dir, new_path)} — {reason}"
        for old, new_path, reason in outcome.renamed
    ]
    lines += [
        f"- marked removed (review and delete by hand): {_link(problem_dir, mark)} — {reason}"
        for mark, reason in outcome.removed
    ]
    if outcome.backup_dir is not None:
        lines.append(f"- previous versions: `{_relative(problem_dir, outcome.backup_dir)}`")
    return ["## Files", "", *lines, ""] if lines else []


def _diffs_section(problem_dir: Path, outcome: Any) -> list[str]:
    """Diffs of the files that existed and changed: overwritten ones (their
    previous version is in the backup) and renamed ones (against the old name)."""
    pairs: list[tuple[Path, Path, Path]] = []  # (previous version, before, after)
    renamed_to = {new_path for _, new_path, _ in outcome.renamed}
    for old, new_path, _ in outcome.renamed:
        before = _backup_of(problem_dir, outcome.backup_dir, old)
        if before is not None:
            pairs.append((before, old, new_path))
    for path in outcome.artifact_paths.values():
        before = _backup_of(problem_dir, outcome.backup_dir, path)
        if path not in renamed_to and before is not None:
            pairs.append((before, path, path))

    lines: list[str] = []
    for before, old, new_path in pairs:
        lines += _diff_block(problem_dir, before, old, new_path)
    return ["## Diffs", "", *lines] if lines else []


def _diff_block(problem_dir: Path, before: Path, old: Path, new_path: Path) -> list[str]:
    """The diff of the previous version `before` of the file `old` against the current `new_path`
    — as a collapsible block; empty if there's no file or it didn't change."""
    if not new_path.is_file():
        return []
    old_name, new_name = _relative(problem_dir, old), _relative(problem_dir, new_path)
    diff = list(
        difflib.unified_diff(
            before.read_text(encoding="utf-8").splitlines(),
            new_path.read_text(encoding="utf-8").splitlines(),
            fromfile=old_name,
            tofile=new_name,
            lineterm="",
        )
    )
    if not diff:
        return []
    title = new_name if old_name == new_name else f"{old_name} -> {new_name}"
    return [
        f"<details><summary><code>{title}</code></summary>",
        "",
        *_fenced("\n".join(diff), "diff"),
        "",
        "</details>",
        "",
    ]


def _backup_of(problem_dir: Path, backup_dir: Path | None, path: Path) -> Path | None:
    if backup_dir is None:
        return None
    try:
        candidate = Path(backup_dir) / Path(path).relative_to(problem_dir)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _relative(problem_dir: Path, path: Path) -> str:
    try:
        return Path(path).relative_to(problem_dir).as_posix()
    except ValueError:
        return str(path)


def _link(problem_dir: Path, path: Path, *, depth: int = 1) -> str:
    """A link to a problem file relative to the report — `depth` directories below
    the problem directory (`llm_reports/` — 1) — clickable in the editor and in a
    Markdown preview."""
    relative = _relative(problem_dir, path)
    target = "../" * depth + relative if not Path(relative).is_absolute() else relative
    return f"[{relative}](<{target}>)"


def _fenced(text: str, language: str) -> list[str]:
    """A code block with a fence longer than any run of backticks in the text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return path
