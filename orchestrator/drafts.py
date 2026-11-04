"""Problem drafts for `orchestrator <problem> init --draft NAME`.

A draft is a free-form text file (any extension) in which
the author describes the problem: the plot, the format, the solution and test ideas — as many as
there are. Drafts lie in `drafts/` (next to `problems/` and `examples/`), at
any depth: subdirectories only group drafts
(`drafts/icpc/my-idea.md`); hidden files and directories aren't drafts. Like
`problems/`, the directory is in `.gitignore` — a draft has the same problem essence as
the spec (CLAUDE.md, "Confidentiality of problems/").

`--draft` accepts the file path relative to `drafts/` in full
(`icpc/my-idea.md`) or without the extension (`icpc/my-idea`); a name without
subdirectories (`my-idea.md`, `my-idea`) is also searched for in all subdirectories.
A path that matched a file in full is chosen right away; otherwise a name that
fits several files is an error, not the first one found.

A module without heavy imports: Tab completion in `cli.py` uses it.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from orchestrator.problem_paths import DRAFT_STEM

DRAFTS_DIR = Path("drafts")

# Under which name (without the extension) the draft copy goes into the problem
# directory: `<problem directory>/authoring/draft<draft extension>`.
PROBLEM_DRAFT_STEM = DRAFT_STEM


class DraftError(ValueError):
    """The draft isn't found or the name is ambiguous."""


def draft_names(drafts_dir: Path) -> list[str]:
    """The paths of draft files relative to `drafts_dir` (joined with `/`, at any
    depth; hidden files and directories, e.g. `.gitkeep`, aren't drafts),
    sorted. No directory — an empty list."""
    drafts_dir = Path(drafts_dir)
    if not drafts_dir.is_dir():
        return []
    names = []
    for path in drafts_dir.rglob("*"):
        rel = path.relative_to(drafts_dir)
        if path.is_file() and not any(part.startswith(".") for part in rel.parts):
            names.append(rel.as_posix())
    return sorted(names)


def _matches(rel: str, name: str) -> bool:
    """Whether the draft `rel` (a path from `draft_names`) fits `name` from
    `--draft`: a path with or without the extension, and a name without subdirectories — also
    in any subdirectory."""
    path = PurePosixPath(rel)
    candidates = {rel, path.with_suffix("").as_posix()}
    if "/" not in name:
        candidates |= {path.name, path.stem}
    return name in candidates


def find_draft(drafts_dir: Path, name: str) -> Path:
    """The draft file by a name with or without the extension (see the module docstring);
    an empty or non-text draft is a `DraftError` too."""
    drafts_dir = Path(drafts_dir)
    names = draft_names(drafts_dir)
    name = name.replace("\\", "/").strip("/")
    matches = [name] if name in names else [n for n in names if _matches(n, name)]
    if len(matches) == 1:
        path = drafts_dir / matches[0]
        try:
            empty = not path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise DraftError(f"failed to read draft {path} as UTF-8 text: {exc}") from exc
        if empty:
            raise DraftError(f"draft {path} is empty")
        return path
    if matches:
        raise DraftError(
            f"draft name '{name}' is ambiguous in {drafts_dir}: {', '.join(matches)} — "
            "pass the path in drafts/ with its extension"
        )
    available = ", ".join(names) if names else "none"
    raise DraftError(f"draft '{name}' not found in {drafts_dir} (available: {available})")


def problem_draft_name(draft_path: Path) -> str:
    """The name of the draft copy in the problem directory (`PROBLEM_DRAFT_STEM`)."""
    return PROBLEM_DRAFT_STEM + Path(draft_path).suffix
