"""Non-ASCII check for the source files a generative step writes.

Some compilers, Polygon's among them, fail on non-ASCII characters in a
source file (a comment in Cyrillic is enough), so generated sources must be
ASCII-only (see CLAUDE.md, "Language"). The check only reports: the file is
written as the model returned it, and the author fixes it.
"""

from __future__ import annotations

from pathlib import Path

# Source files Polygon compiles or runs; other artifacts (`.tex`, `.yaml`)
# are text in the problem's language.
SOURCE_SUFFIXES = frozenset({".c", ".cpp", ".h", ".hpp", ".java", ".kt", ".py"})
SOURCE_NAMES = frozenset({"test_script", "test_script_groups"})


def is_source_file(path: Path) -> bool:
    return path.suffix in SOURCE_SUFFIXES or path.name in SOURCE_NAMES


def non_ascii_lines(text: str) -> list[tuple[int, str]]:
    """Lines (1-based number, text) that contain non-ASCII characters."""
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if not line.isascii()
    ]


def charset_error(path: Path) -> str | None:
    """A message listing the non-ASCII lines of `path`, or `None` if it has
    none or isn't a source file."""
    if not is_source_file(path):
        return None
    lines = non_ascii_lines(path.read_text(encoding="utf-8", errors="replace"))
    if not lines:
        return None
    listing = "\n".join(f"line {number}: {line.strip()}" for number, line in lines)
    return "non-ASCII characters (some compilers, Polygon's among them, fail on them):\n" + listing
