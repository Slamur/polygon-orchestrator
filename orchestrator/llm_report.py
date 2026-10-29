"""Человеко-читаемые отчёты о работе модели: `llm_reports/<шаг>.md` в
каталоге задачи.

Отчёт — текущее состояние шага, а не история: файл шага переписывается
только когда шаг в прогоне реально отработал (вызвал модель или применил
сохранённый ответ, `StepOutcome.ran`); у пропущенного по кэшу шага остаётся
отчёт его последнего прогона. История прогонов — в `llm_generation.log`.

В отчёте сначала то, что требует действий автора (`uncertain`, не
применённое, ошибки компиляции, `proposed`), потом идеи модели
(`suggestion`), что она изменила, файлы шага и диффы изменённых файлов
против прежних версий из `.llm_backup/`. `init --draft` пишет так же
`llm_reports/spec_from_draft.md`, `llm accept` — `llm_reports/accept.md`
(что принято и что изменилось во входе каждого шага).
"""

from __future__ import annotations

import datetime
import difflib
import re
from pathlib import Path
from typing import Any, Optional

from orchestrator.steps.base import CONFLICTS_REPORT

REPORTS_DIRNAME = "llm_reports"
SPEC_FROM_DRAFT_REPORT = "spec_from_draft"
ACCEPT_REPORT = "accept"

# Порядок и заголовки групп заметок; заметки других `kind` — в конце, в
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
    """`2 proposed, 1 changed` — сколько заметок какого вида, в порядке
    `_NOTE_SECTIONS`; пусто, если заметок нет."""
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
    """Пишет `llm_reports/<шаг>.md` по `pipeline.StepOutcome` отработавшего
    шага; возвращает путь отчёта."""
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
    """Пишет `llm_reports/spec_from_draft.md` по `SpecFromDraftResult`."""
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
    lines += _notes_sections(result.notes)
    before = None if result.path is None else _backup_of(problem_dir, result.backup_dir, result.path)
    if before is not None:
        diff = _diff_block(problem_dir, before, result.path, result.path)
        lines += ["## Diffs", "", *diff] if diff else []
    if result.backup_dir is not None:
        lines += [f"Previous versions: `{_relative(problem_dir, result.backup_dir)}/`", ""]
    return _write(report_path(problem_dir, SPEC_FROM_DRAFT_REPORT), lines)


def write_accept_report(problem_dir: Path, statuses: list[Any], *, command_line: str) -> Path:
    """Пишет `llm_reports/accept.md` по `pipeline.StepStatus` шагов, которые
    `llm accept` принял или не принял: полное `detail` каждого — в консоль
    идёт короткое `brief`."""
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


def _notes_sections(notes: list[dict[str, Any]]) -> list[str]:
    known = {kind for kind, _ in _NOTE_SECTIONS}
    sections = [(title, [n for n in notes if n.get("kind") == kind]) for kind, title in _NOTE_SECTIONS]
    sections.append((_OTHER_NOTES_TITLE, [n for n in notes if n.get("kind") not in known]))
    lines: list[str] = []
    for title, group in sections:
        if not group:
            continue
        lines += [f"## {title} ({len(group)})", ""]
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
    """Диффы файлов, которые существовали и изменились: перезаписанных (их
    прежняя версия — в бэкапе) и переименованных (против старого имени)."""
    pairs: list[tuple[Path, Path, Path]] = []  # (прежняя версия, было, стало)
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
    """Дифф прежней версии `before` файла `old` против текущего `new_path`
    — сворачиваемым блоком; пусто, если файла нет или он не изменился."""
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


def _backup_of(problem_dir: Path, backup_dir: Optional[Path], path: Path) -> Optional[Path]:
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


def _link(problem_dir: Path, path: Path) -> str:
    """Ссылка на файл задачи относительно `llm_reports/` — кликабельна в
    редакторе и в просмотре Markdown."""
    relative = _relative(problem_dir, path)
    target = f"../{relative}" if not Path(relative).is_absolute() else relative
    return f"[{relative}](<{target}>)"


def _fenced(text: str, language: str) -> list[str]:
    """Блок кода с ограждением длиннее любой серии обратных кавычек в тексте."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return path
