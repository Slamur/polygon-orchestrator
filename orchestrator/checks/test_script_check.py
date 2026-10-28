"""Проверка строк раскрытого FreeMarker test-script'а — до загрузки в
Polygon (`orchestrator <задача> polygon check` и проверка перед загрузкой в
`upload_test_script`).

Polygon раскрывает скрипт в построчный список команд и отказывается
генерировать тесты, если две строки совпали. Ищется именно совпадение строк
скрипта, а не одинаковые тесты.

Ещё Polygon не принимает строки, где параметры генератора содержат кавычки
или `>`: строка — `<генератор> <параметры> [| <генератор> ...] > <тест>`,
и `>` в ней ровно один — перенаправление в тест (`find_invalid_lines`).
Исправить это механически нельзя: значение с пробелом (`-p="1 2"`) должно
стать одним словом, а это меняет и разбор параметров в генераторе.

Скрипт раскрывается настоящим FreeMarker (`FreemarkerRender.java`, запуск
`java` без сборки; jar скачивается с Maven Central в кэш при первом запуске,
см. `freemarker_jar`), а не собственным интерпретатором: любое расхождение в
семантике давало бы ложные срабатывания или пропуски.

Чтобы для каждой строки результата знать, какая строка скрипта её
напечатала, скрипт перед раскрытием размечается (`instrument_script`): в
конец каждой строки скрипта, печатающей текст, дописывается маркер с её
номером и значениями переменных охватывающих `<#list ... as x>`. Маркеры
дописываются в конец тех же строк, поэтому номера строк в ошибках
FreeMarker остаются номерами исходного файла. После раскрытия маркеры
отрезаются (`parse_rendered`), строки сравниваются без пробелов по краям,
с пробелами внутри, схлопнутыми до одного, — пустые строки Polygon
выкидывает сам.

Разметка — построчный разбор директив, а не полный парсер FreeMarker:
строка, где цикл открывается и закрывается в ней же, получает только номер
без значений переменной цикла; тела `<#function>` пропускаются (функции
ничего не печатают), в теле `<#macro>` переменные внешних циклов не видны.
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
# Путь к уже скачанному jar — вместо кэша (например, без доступа к сети).
FREEMARKER_JAR_ENV = "ORCHESTRATOR_FREEMARKER_JAR"
# Непустое значение выключает проверку перед загрузкой в `upload_test_script`
# (на случай, если проверка ошиблась, а скрипт нужно загрузить).
SKIP_CHECK_ENV = "ORCHESTRATOR_SKIP_SCRIPT_CHECK"

_RENDERER = Path(__file__).with_name("FreemarkerRender.java")
_RENDER_TIMEOUT_SECONDS = 60.0

# Маркер строки скрипта: \x1e<номер строки>\x1f<значение>\x1f<значение>...\x1e.
_MARK = "\x1e"
_SEP = "\x1f"
_VALUE_FUNCTION = "__dupcheck_value"
# Функция, превращающая значение переменной цикла в текст маркера. Функции
# FreeMarker видны во всём шаблоне, поэтому она дописывается в конец
# скрипта — номера строк не сдвигаются.
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
    """Проверку нельзя выполнить: нет `java` или не удалось получить jar FreeMarker."""


class ScriptRenderError(Exception):
    """FreeMarker не смог раскрыть скрипт (синтаксис, неизвестная переменная…)."""


@dataclass(frozen=True)
class Loop:
    """Цикл `<#list ... as x>`, охватывающий строку скрипта."""

    line: int  # 1-based номер строки с открывающей директивой
    header: str  # текст директивы, `<#list counts as c>`
    variables: tuple[str, ...]


@dataclass(frozen=True)
class SourceLine:
    """Строка скрипта, печатающая текст, и охватывающие её циклы."""

    line: int
    text: str
    loops: tuple[Loop, ...]

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(name for loop in self.loops for name in loop.variables)


@dataclass(frozen=True)
class ScriptLine:
    """Строка раскрытого скрипта. `source` — напечатавшая её строка скрипта
    (`None`, если разметка её не опознала), `values` — значения переменных
    `source.variables` в этой итерации."""

    index: int  # 1-based номер среди непустых строк результата
    text: str
    source: SourceLine | None = None
    values: tuple[str, ...] = ()


@dataclass
class DuplicateGroup:
    text: str
    occurrences: list[ScriptLine] = field(default_factory=list)


@dataclass(frozen=True)
class InvalidLine:
    """Строка раскрытого скрипта, которую Polygon не примет, и почему."""

    line: ScriptLine
    problems: tuple[str, ...]


@dataclass(frozen=True)
class InstrumentedScript:
    source: str
    sources: dict[int, SourceLine]


def instrument_script(source: str) -> InstrumentedScript:
    """Скрипт с маркерами в конце каждой строки, печатающей текст (см.
    докстринг модуля), и описание этих строк по номерам."""
    out_lines: list[str] = []
    sources: dict[int, SourceLine] = {}
    # Стек открытых блоков: ("list"|"items", Loop | None) / ("function"|"macro", None).
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
        # Маркер — только если строка кончается вне директивы/комментария/${}.
        if not prints_text or "function" in kinds or not scanner.in_text:
            out_lines.append(line)
            continue

        # Внутри макроса переменные циклов вне его не видны.
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
        # Закрывается последний открытый блок того же вида.
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
    """Посимвольный разбор шаблона на текст, директивы (`<#...>`, `</#...>`,
    `<@...>`) и интерполяции `${...}` (считаются текстом — они печатают)
    с сохранением состояния между строками: директива, комментарий или
    интерполяция могут занимать несколько строк."""

    def __init__(self) -> None:
        self.mode = "text"  # text | tag | comment | interp
        self.buffer = ""
        self.quote = ""
        self.depth = 0

    @property
    def in_text(self) -> bool:
        return self.mode == "text"

    def feed(self, line: str) -> list[tuple[str, str]]:
        """Части строки: ("text", ...) и ("tag", полный текст директивы —
        выдаётся в строке, где директива закрылась)."""
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
    """Непустые строки раскрытого размеченного скрипта: текст без маркеров и
    строка скрипта, которая его напечатала (первый маркер в строке)."""
    result: list[ScriptLine] = []
    for raw in rendered.replace("\r\n", "\n").split("\n"):
        parts = raw.split(_MARK)
        # Нечётные части — содержимое маркеров, чётные — текст.
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
    """Группы одинаковых строк — в порядке первого появления."""
    groups: dict[str, DuplicateGroup] = {}
    for line in lines:
        groups.setdefault(line.text, DuplicateGroup(line.text)).occurrences.append(line)
    return [group for group in groups.values() if len(group.occurrences) > 1]


def find_invalid_lines(lines: list[ScriptLine]) -> list[InvalidLine]:
    """Строки с кавычками или не ровно одним `>` (см. докстринг модуля)."""
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
    """Раскрывает скрипт настоящим FreeMarker и возвращает его непустые строки
    с привязкой к строкам скрипта."""
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
    """Убирает маркеры разметки из текста ошибки FreeMarker (он цитирует
    фрагменты шаблона)."""
    text = re.sub(_MARK + r"\d+[^" + _MARK + r"]*" + _MARK, "", text)
    return text.replace(_MARK, "").replace(_SEP, " ")


def freemarker_jar(*, download: bool = True) -> Path:
    """jar FreeMarker: из `ORCHESTRATOR_FREEMARKER_JAR` или из кэша
    (`~/.cache/polygon-orchestrator/`), куда при отсутствии скачивается с Maven
    Central с проверкой sha1."""
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
    """Отчёт: каждая повторяющаяся строка, сколько раз она встретилась и какая
    строка скрипта (в каком цикле, при каких значениях переменных) её
    напечатала."""
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
    """Отчёт по строкам, которые Polygon не примет, — по строке скрипта,
    напечатавшей их, с первым примером раскрытой строки."""
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
            for name, value, loop_line in zip(line.source.variables, line.values, loop_lines)
        )
        text += f"   [{values}]"
    return text


@dataclass(frozen=True)
class ScriptCheckResult:
    duplicates: list[DuplicateGroup]
    report: str  # пусто, если проблем нет
    invalid: list[InvalidLine] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.duplicates or self.invalid)


def check_script(source: str, script_name: str, *, jar: Path | None = None) -> ScriptCheckResult:
    """Раскрывает скрипт и ищет одинаковые строки и строки, которые Polygon
    не примет. Исключения — `ScriptCheckUnavailable` и `ScriptRenderError`."""
    lines = expand_script(source, jar=jar)
    duplicates = find_duplicates(lines)
    invalid = find_invalid_lines(lines)
    reports = []
    if invalid:
        reports.append(format_invalid_lines(invalid, script_name))
    if duplicates:
        reports.append(format_duplicates(duplicates, script_name))
    return ScriptCheckResult(duplicates, "\n".join(reports), invalid)
