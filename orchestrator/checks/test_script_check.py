"""Checking the lines of an expanded FreeMarker test-script — before uploading to
Polygon (`orchestrator <problem> local check` and the check before uploading in
`upload_test_script`).

Polygon expands the script into a line-by-line list of commands and refuses to
generate tests if two lines match. It's exactly the matching of script lines
that is searched for, not identical tests.

Polygon also doesn't accept lines where the generator's parameters contain quotes
or `>`: a line is `<generator> <parameters> [| <generator> ...] > <test>`,
and there's exactly one `>` in it — the redirection to the test (`find_invalid_lines`).
This can't be fixed mechanically: a value with a space (`-p="1 2"`) has to
become one word, and that changes the parsing of parameters in the generator too.

The script is expanded with real FreeMarker (`FreemarkerRender.java`, run with
`java` without building; the jar is downloaded from Maven Central into a cache on the first run,
see `freemarker_jar`), not with our own interpreter: any discrepancy in
semantics would give false positives or misses.

To know for each line of the result which script line
printed it, the script is marked up before expanding (`instrument_script`): at
the end of each script line that prints text, a marker with its
number and the values of the variables of the enclosing `<#list ... as x>` is appended. The markers
are appended at the end of the same lines, so the line numbers in
FreeMarker errors stay the line numbers of the source file. After expanding, the markers
are cut off (`parse_rendered`), the lines are compared without spaces at the ends,
with the spaces inside collapsed to one — Polygon drops empty lines
itself.

The markup is a line-by-line parse of directives, not a full FreeMarker parser:
a line where a loop opens and closes within it gets only the number
without the values of the loop variable; the bodies of `<#function>` are skipped (functions
print nothing), in the body of a `<#macro>` the variables of outer loops aren't visible.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

FREEMARKER_VERSION = "2.3.33"
_FREEMARKER_SHA1 = "fecaeb606993fc9fd0f95fe5a644048a69c39474"
_FREEMARKER_URL = (
    "https://repo1.maven.org/maven2/org/freemarker/freemarker/"
    f"{FREEMARKER_VERSION}/freemarker-{FREEMARKER_VERSION}.jar"
)
# The path to an already downloaded jar — instead of the cache (e.g. without network access).
FREEMARKER_JAR_ENV = "ORCHESTRATOR_FREEMARKER_JAR"
# A non-empty value turns off the check before uploading in `upload_test_script`
# (in case the check is wrong and the script has to be uploaded).
SKIP_CHECK_ENV = "ORCHESTRATOR_SKIP_SCRIPT_CHECK"

_RENDERER = Path(__file__).with_name("FreemarkerRender.java")
_RENDER_TIMEOUT_SECONDS = 60.0

# A script line marker: \x1e<line number>\x1f<value>\x1f<value>...\x1e.
_MARK = "\x1e"
_SEP = "\x1f"
_VALUE_FUNCTION = "__dupcheck_value"
# A function that turns a loop variable's value into the marker text. FreeMarker
# functions are visible in the whole template, so it's appended at the end of the
# script — the line numbers don't shift.
_VALUE_FUNCTION_SOURCE = (
    f"<#function {_VALUE_FUNCTION} v>"
    "<#if v?is_number || v?is_boolean><#return v?c>"
    "<#elseif v?is_string><#return v>"
    '<#else><#return "?"></#if>'
    "</#function>"
)

_LOOP_AS_RE = re.compile(r"\bas\s+(\w+)(?:\s*,\s*(\w+))?")
_BLOCK_RE = re.compile(r"<(/?)#(list|items|function|macro)\b")


class ScriptCheckUnavailable(Exception):
    """The check can't be run: there's no `java` or the FreeMarker jar couldn't be obtained."""


class ScriptRenderError(Exception):
    """FreeMarker couldn't expand the script (syntax, an unknown variable…)."""


@dataclass(frozen=True)
class Loop:
    """A `<#list ... as x>` loop enclosing a script line."""

    line: int  # 1-based number of the line with the opening directive
    header: str  # the directive text, `<#list counts as c>`
    variables: tuple[str, ...]


@dataclass(frozen=True)
class SourceLine:
    """A script line that prints text, and the loops enclosing it."""

    line: int
    text: str
    loops: tuple[Loop, ...]

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(name for loop in self.loops for name in loop.variables)


@dataclass(frozen=True)
class ScriptLine:
    """A line of the expanded script. `source` — the script line that printed it
    (`None` if the markup didn't recognize it), `values` — the values of the variables
    `source.variables` in this iteration."""

    index: int  # 1-based number among the non-empty lines of the result
    text: str
    source: SourceLine | None = None
    values: tuple[str, ...] = ()


@dataclass
class DuplicateGroup:
    text: str
    occurrences: list[ScriptLine] = field(default_factory=list)


@dataclass(frozen=True)
class InvalidLine:
    """A line of the expanded script that Polygon won't accept, and why."""

    line: ScriptLine
    problems: tuple[str, ...]


@dataclass(frozen=True)
class InstrumentedScript:
    source: str
    sources: dict[int, SourceLine]


def instrument_script(source: str) -> InstrumentedScript:
    """The script with markers at the end of each line that prints text (see the
    module docstring), and a description of these lines by number."""
    out_lines: list[str] = []
    sources: dict[int, SourceLine] = {}
    # The stack of open blocks: ("list"|"items", Loop | None) / ("function"|"macro", None).
    stack: list[tuple[str, Loop | None]] = []
    scanner = _Scanner()

    for number, line in enumerate(source.split("\n"), start=1):
        prints_text = False
        for kind, text in scanner.feed(line):
            if kind == "text":
                prints_text = prints_text or bool(text.strip())
            else:
                _apply_tag(stack, text, number)

        kinds = [kind for kind, _ in stack]
        # A marker — only if the line ends outside a directive/comment/${}.
        if not prints_text or "function" in kinds or not scanner.in_text:
            out_lines.append(line)
            continue

        # Inside a macro the variables of loops outside it aren't visible.
        scope = stack[_last_index(kinds, "macro") + 1 :]
        loops = tuple(loop for _, loop in scope if loop is not None)
        source_line = SourceLine(number, line.strip(), loops)
        sources[number] = source_line
        marker = _MARK + str(number)
        for name in source_line.variables:
            marker += _SEP + "${" + _VALUE_FUNCTION + "(" + name + ")}"
        out_lines.append(line + marker + _MARK)

    instrumented = "\n".join(out_lines) + "\n" + _VALUE_FUNCTION_SOURCE
    return InstrumentedScript(instrumented, sources)


def _apply_tag(stack: list[tuple[str, Loop | None]], tag: str, number: int) -> None:
    match = _BLOCK_RE.match(tag)
    if match is None:
        return
    closing, kind = match.group(1), match.group(2)
    if closing:
        # The last open block of the same kind is closed.
        position = _last_index([open_kind for open_kind, _ in stack], kind)
        if position >= 0:
            del stack[position:]
        return
    loop = None
    as_match = _LOOP_AS_RE.search(tag) if kind in ("list", "items") else None
    if as_match:
        loop = Loop(number, " ".join(tag.split()), tuple(n for n in as_match.groups() if n))
    stack.append((kind, loop))


class _Scanner:
    """A character-by-character parse of the template into text, directives (`<#...>`, `</#...>`,
    `<@...>`) and `${...}` interpolations (counted as text — they print)
    keeping the state between lines: a directive, a comment or an
    interpolation can span several lines."""

    def __init__(self) -> None:
        self.mode = "text"  # text | tag | comment | interp
        self.buffer = ""
        self.quote = ""
        self.depth = 0

    @property
    def in_text(self) -> bool:
        return self.mode == "text"

    def feed(self, line: str) -> list[tuple[str, str]]:
        """The parts of a line: ("text", ...) and ("tag", the full text of the directive —
        yielded in the line where the directive closed)."""
        parts: list[tuple[str, str]] = []
        text = ""
        i = 0
        while i < len(line):
            ch = line[i]
            if self.mode == "text":
                if line.startswith("<#--", i):
                    self.mode, i = "comment", i + 4
                    continue
                if line.startswith(("<#", "</#", "<@", "</@"), i):
                    parts.append(("text", text))
                    text, self.mode, self.buffer = "", "tag", ""
                    continue
                if line.startswith("${", i):
                    self.mode, self.depth, text, i = "interp", 1, text + "${", i + 2
                    continue
                text += ch
            elif self.mode == "comment":
                if line.startswith("-->", i):
                    self.mode, i = "text", i + 3
                    continue
            elif self.quote:
                if self.mode == "tag":
                    self.buffer += ch
                if ch == "\\":
                    if self.mode == "tag" and i + 1 < len(line):
                        self.buffer += line[i + 1]
                    i += 2
                    continue
                if ch == self.quote:
                    self.quote = ""
            elif ch in "\"'":
                self.quote = ch
                if self.mode == "tag":
                    self.buffer += ch
            elif self.mode == "tag":
                self.buffer += ch
                if ch == ">":
                    parts.append(("tag", self.buffer))
                    self.mode = "text"
            else:  # interp
                text += ch
                if ch == "{":
                    self.depth += 1
                elif ch == "}":
                    self.depth -= 1
                    if self.depth == 0:
                        self.mode = "text"
            i += 1
        if self.mode == "tag":
            self.buffer += "\n"
        parts.append(("text", text))
        return parts


def _last_index(items: list[str], value: str) -> int:
    for position in range(len(items) - 1, -1, -1):
        if items[position] == value:
            return position
    return -1


def parse_rendered(rendered: str, sources: dict[int, SourceLine]) -> list[ScriptLine]:
    """The non-empty lines of the expanded marked-up script: the text without markers and
    the script line that printed it (the first marker in the line)."""
    result: list[ScriptLine] = []
    for raw in rendered.replace("\r\n", "\n").split("\n"):
        parts = raw.split(_MARK)
        # Odd parts are the contents of markers, even ones are text.
        text = " ".join("".join(parts[0::2]).split())
        if not text:
            continue
        source, values = None, ()
        if len(parts) > 1:
            fields = parts[1].split(_SEP)
            source = sources.get(int(fields[0])) if fields[0].isdigit() else None
            values = tuple(fields[1:])
        result.append(ScriptLine(len(result) + 1, text, source, values))
    return result


def find_duplicates(lines: list[ScriptLine]) -> list[DuplicateGroup]:
    """Groups of identical lines — in the order of first appearance."""
    groups: dict[str, DuplicateGroup] = {}
    for line in lines:
        groups.setdefault(line.text, DuplicateGroup(line.text)).occurrences.append(line)
    return [group for group in groups.values() if len(group.occurrences) > 1]


def find_invalid_lines(lines: list[ScriptLine]) -> list[InvalidLine]:
    """Lines with quotes or not exactly one `>` (see the module docstring)."""
    invalid: list[InvalidLine] = []
    for line in lines:
        problems: list[str] = []
        quotes = sorted({ch for ch in line.text if ch in "\"'`"})
        if quotes:
            problems.append("quotes " + " ".join(quotes))
        redirects = line.text.count(">")
        if redirects == 0:
            problems.append("no '> test' redirect")
        elif redirects > 1:
            problems.append(f"{redirects} '>' (only the final '> test' is allowed)")
        if problems:
            invalid.append(InvalidLine(line, tuple(problems)))
    return invalid


def expand_script(source: str, *, jar: Path | None = None) -> list[ScriptLine]:
    """Expands the script with real FreeMarker and returns its non-empty lines
    bound to the script lines."""
    instrumented = instrument_script(source)
    rendered = render_freemarker(instrumented.source, jar=jar)
    return parse_rendered(rendered, instrumented.sources)


def render_freemarker(source: str, *, jar: Path | None = None) -> str:
    java = shutil.which("java")
    if java is None:
        raise ScriptCheckUnavailable("java not found in PATH (needed to expand FreeMarker)")
    jar = jar or freemarker_jar()
    with tempfile.TemporaryDirectory(prefix="test_script_check_") as tmp:
        script = Path(tmp) / "test_script"
        script.write_text(source, encoding="utf-8")
        try:
            completed = subprocess.run(
                [java, "-cp", str(jar), str(_RENDERER), str(script)],
                capture_output=True,
                timeout=_RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScriptRenderError(
                f"FreeMarker did not finish in {_RENDER_TIMEOUT_SECONDS:.0f}s"
            ) from exc
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    if completed.returncode == 2:
        raise ScriptRenderError(_clean_markers(stderr))
    if completed.returncode != 0:
        raise ScriptCheckUnavailable(f"java failed (exit {completed.returncode}): {stderr}")
    return completed.stdout.decode("utf-8")


def _clean_markers(text: str) -> str:
    """Removes the markup markers from the text of a FreeMarker error (it quotes
    fragments of the template)."""
    text = re.sub(_MARK + r"\d+[^" + _MARK + r"]*" + _MARK, "", text)
    return text.replace(_MARK, "").replace(_SEP, " ")


def freemarker_jar(*, download: bool = True) -> Path:
    """The FreeMarker jar: from `ORCHESTRATOR_FREEMARKER_JAR` or from the cache
    (`~/.cache/polygon-orchestrator/`), into which, if absent, it's downloaded from Maven
    Central with a sha1 check."""
    override = os.environ.get(FREEMARKER_JAR_ENV)
    if override:
        path = Path(override)
        if not path.is_file():
            raise ScriptCheckUnavailable(f"{FREEMARKER_JAR_ENV}={override}: file not found")
        return path

    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = cache_root / "polygon-orchestrator" / f"freemarker-{FREEMARKER_VERSION}.jar"
    if path.is_file():
        return path
    if not download:
        raise ScriptCheckUnavailable(f"{path} not found")
    try:
        with urllib.request.urlopen(_FREEMARKER_URL, timeout=60) as response:
            content = response.read()
    except OSError as exc:
        raise ScriptCheckUnavailable(f"failed to download {_FREEMARKER_URL}: {exc}") from exc
    if hashlib.sha1(content).hexdigest() != _FREEMARKER_SHA1:
        raise ScriptCheckUnavailable(f"sha1 mismatch for {_FREEMARKER_URL}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jar.part")
    tmp.write_bytes(content)
    tmp.replace(path)
    return path


def format_duplicates(groups: list[DuplicateGroup], script_name: str) -> str:
    """The report: each repeated line, how many times it occurred and which
    script line (in which loop, with which variable values)
    printed it."""
    total = sum(len(group.occurrences) for group in groups)
    lines = [
        f"{script_name}: {len(groups)} line(s) repeated ({total} occurrences) — "
        "Polygon refuses to generate tests from a script with identical lines:"
    ]
    for group in groups:
        lines.append(f'  "{group.text}" — {len(group.occurrences)} times:')
        for occurrence in group.occurrences:
            lines.append("    " + _describe(occurrence))
    return "\n".join(lines)


def format_invalid_lines(invalid: list[InvalidLine], script_name: str) -> str:
    """The report on lines Polygon won't accept — by the script line that
    printed them, with the first example of an expanded line."""
    groups: dict[int | str, list[InvalidLine]] = {}
    for item in invalid:
        key = item.line.source.line if item.line.source else item.line.text
        groups.setdefault(key, []).append(item)
    lines = [
        f"{script_name}: {len(invalid)} line(s) Polygon will reject — generator parameters "
        "can't contain quotes or '>' (pass a value with spaces as one word, e.g. "
        "comma-separated, or as separate parameters):"
    ]
    for items in groups.values():
        first = items[0]
        problems = "; ".join(dict.fromkeys(p for item in items for p in item.problems))
        source = first.line.source
        where = f"line {source.line}: {source.text}" if source else "source line unknown"
        lines.append(f"  {where} — {problems}")
        lines.append(f'    {len(items)} line(s), e.g. #{first.line.index} "{first.line.text}"')
    return "\n".join(lines)


def _describe(line: ScriptLine) -> str:
    """`#3 <- line 10: gen -c=${c} > $   [c=100 (loop at line 8), k=1 (loop at line 9)]`."""
    head = f"#{line.index} <- "
    if line.source is None:
        return head + "source line unknown"
    text = f"{head}line {line.source.line}: {line.source.text}"
    loop_lines = [loop.line for loop in line.source.loops for _ in loop.variables]
    if loop_lines and len(line.values) == len(loop_lines):
        values = ", ".join(
            f"{name}={value} (loop at line {loop_line})"
            for name, value, loop_line in zip(
                line.source.variables, line.values, loop_lines, strict=False
            )
        )
        text += f"   [{values}]"
    return text


@dataclass(frozen=True)
class ScriptCheckResult:
    duplicates: list[DuplicateGroup]
    report: str  # empty if there are no problems
    invalid: list[InvalidLine] = field(default_factory=list)
    lines: list[ScriptLine] = field(default_factory=list)  # the expanded script

    @property
    def failed(self) -> bool:
        return bool(self.duplicates or self.invalid)


def check_script(source: str, script_name: str, *, jar: Path | None = None) -> ScriptCheckResult:
    """Expands the script and looks for identical lines and lines Polygon
    won't accept. Exceptions — `ScriptCheckUnavailable` and `ScriptRenderError`."""
    lines = expand_script(source, jar=jar)
    duplicates = find_duplicates(lines)
    invalid = find_invalid_lines(lines)
    reports = []
    if invalid:
        reports.append(format_invalid_lines(invalid, script_name))
    if duplicates:
        reports.append(format_duplicates(duplicates, script_name))
    return ScriptCheckResult(duplicates, "\n".join(reports), invalid, lines)
