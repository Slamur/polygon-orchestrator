import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator import cli
from orchestrator.checks import test_script_check as check
from orchestrator.checks.test_script_check import (
    SKIP_CHECK_ENV,
    DuplicateGroup,
    ScriptCheckResult,
    ScriptCheckUnavailable,
    ScriptLine,
    ScriptRenderError,
    find_duplicates,
    format_duplicates,
    instrument_script,
    parse_rendered,
)
from orchestrator.polygon.state import PolygonState, save_polygon_state
from orchestrator.polygon.steps import upload_test_script
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.spec import load_spec

from polygon_sync_helpers import run_and_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROBLEM_ID = "example-problem"

DUPLICATES_SCRIPT = """<#assign maxN = 100/>
<#assign counts = [10, maxN, 100]/>
<#list counts as c>
    gen -n=${maxN} -c=${c} > $
</#list>
gen -n=100 > $
gen  -n=100  >  $
"""


def _marked(source: str) -> set[int]:
    return set(instrument_script(source).sources)


def _jar_or_skip() -> Path:
    if shutil.which("java") is None:
        pytest.skip("java not found")
    try:
        # без скачивания: тесты не ходят в сеть
        return check.freemarker_jar(download=False)
    except ScriptCheckUnavailable as exc:
        pytest.skip(str(exc))


# --- разметка ---------------------------------------------------------------


def test_marks_only_lines_printing_text():
    source = '<#assign a = 1/>\ngen -a=${a} > $\n<#if a gt 0>\n</#if>\n\n${a}\n'
    assert _marked(source) == {2, 6}


def test_marker_keeps_line_count_and_line_contents():
    source = "<#list 1..2 as i>\n  gen ${i} > $\n</#list>\n"
    instrumented = instrument_script(source).source.split("\n")
    original = source.split("\n")
    assert all(new.startswith(old) for old, new in zip(original, instrumented))
    assert len(instrumented) == len(original) + 1  # функция значения — в конце


def test_records_enclosing_loops_and_variables():
    source = (
        "<#list counts as c>\n"
        "  <#list pairs as k, v>\n"
        "    gen ${c} ${k} > $\n"
        "  </#list>\n"
        "  gen ${c} > $\n"
        "</#list>\n"
        "gen > $\n"
    )
    sources = instrument_script(source).sources
    assert sources[3].variables == ("c", "k", "v")
    assert [loop.line for loop in sources[3].loops] == [1, 2]
    assert sources[3].loops[0].header == "<#list counts as c>"
    assert sources[5].variables == ("c",)
    assert sources[7].variables == ()


def test_no_marker_inside_multiline_directive_comment_or_function():
    source = (
        "<#assign xs = [1,\n"  # 1: директива не закрыта
        '   "a > b", 2]/>\n'  # 2: `>` внутри строки директиву не закрывает
        "<#-- comment\n"  # 3
        "  gen > $\n"  # 4: внутри комментария
        "-->\n"  # 5
        "<#function f x>\n"  # 6
        "  text ${x}\n"  # 7: функции ничего не печатают
        "  <#return x/>\n"
        "</#function>\n"
        "gen ${f(1)} > $\n"  # 10
    )
    assert _marked(source) == {10}


def test_loop_variables_are_not_visible_inside_macro():
    source = "<#list xs as x>\n<#macro m>\n  gen > $\n</#macro>\n</#list>\n"
    assert instrument_script(source).sources[3].variables == ()


# --- разбор результата ------------------------------------------------------


def test_parse_rendered_strips_markers_and_normalizes_whitespace():
    sources = instrument_script("<#list xs as x>\n gen ${x} > $\n</#list>\n").sources
    rendered = " gen  1 > $\x1e2\x1f1\x1e\n\n   \nraw  line\n"
    lines = parse_rendered(rendered, sources)

    assert [line.text for line in lines] == ["gen 1 > $", "raw line"]
    assert lines[0].source is sources[2] and lines[0].values == ("1",)
    assert lines[1].source is None and lines[1].index == 2


def test_find_duplicates_groups_in_order_of_first_occurrence():
    lines = [ScriptLine(i, text) for i, text in enumerate(["a", "b", "a", "c", "b", "a"], 1)]
    groups = find_duplicates(lines)
    assert [(g.text, [o.index for o in g.occurrences]) for g in groups] == [
        ("a", [1, 3, 6]),
        ("b", [2, 5]),
    ]


def test_report_names_source_lines_and_loop_values():
    sources = instrument_script("<#list xs as x>\n gen > $\n</#list>\ngen > $\n").sources
    lines = [
        ScriptLine(1, "gen > $", sources[2], ("7",)),
        ScriptLine(2, "gen > $", sources[4]),
    ]
    report = format_duplicates(find_duplicates(lines), "test_script")

    assert '"gen > $" — 2 times' in report
    assert "#1 <- line 2: gen > $   [x=7 (loop at line 1)]" in report
    assert "#2 <- line 4: gen > $" in report


# --- раскрытие настоящим FreeMarker ----------------------------------------


def test_expand_and_find_duplicates_with_freemarker():
    jar = _jar_or_skip()
    result = check.check_script(DUPLICATES_SCRIPT, "test_script", jar=jar)

    assert [(g.text, [o.index for o in g.occurrences]) for g in result.duplicates] == [
        ("gen -n=100 -c=100 > $", [2, 3]),
        ("gen -n=100 > $", [4, 5]),
    ]
    assert "c=100 (loop at line 3)" in result.report
    assert "line 7: gen  -n=100  >  $" in result.report


def test_render_error_points_to_source_line():
    jar = _jar_or_skip()
    with pytest.raises(ScriptRenderError, match="line 2") as error:
        check.expand_script("<#list 1..2 as i>\n${missing} > $\n</#list>\n", jar=jar)
    assert "\x1e" not in str(error.value)


@pytest.mark.parametrize("script", sorted((Path(__file__).parent.parent / "examples").glob("*/*/test_script")), ids=str)
def test_example_scripts_have_no_identical_lines(script):
    jar = _jar_or_skip()
    lines = check.expand_script(script.read_text(encoding="utf-8"), jar=jar)
    assert lines and all(line.source is not None for line in lines)
    assert find_duplicates(lines) == []


def test_freemarker_jar_from_env(monkeypatch, tmp_path):
    jar = tmp_path / "fm.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv(check.FREEMARKER_JAR_ENV, str(jar))
    assert check.freemarker_jar() == jar

    monkeypatch.setenv(check.FREEMARKER_JAR_ENV, str(tmp_path / "missing.jar"))
    with pytest.raises(ScriptCheckUnavailable):
        check.freemarker_jar()


# --- проверка перед загрузкой в Polygon -------------------------------------


@pytest.fixture
def flat_spec():
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    return spec.model_copy(
        update={"generation": spec.generation.model_copy(update={"script_style": "flat"})}
    )


@pytest.fixture
def linked_problem(tmp_path):
    problems_dir = tmp_path / "problems"
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(problem_id=PROBLEM_ID, polygon_id=1, created_at="2026-09-19T12:00:00+00:00"),
        problems_dir=problems_dir,
    )
    (problems_dir / PROBLEM_ID / "test_script").write_text("gen > $\ngen > $\n", encoding="utf-8")
    return problems_dir


def _duplicates_result():
    group = DuplicateGroup("gen > $", [ScriptLine(1, "gen > $"), ScriptLine(2, "gen > $")])
    return ScriptCheckResult([group], "test_script: REPORT")


def test_upload_stops_on_duplicates_before_network(monkeypatch, linked_problem, flat_spec):
    monkeypatch.delenv(SKIP_CHECK_ENV)
    monkeypatch.setattr(upload_test_script, "check_script", lambda *a, **k: _duplicates_result())
    client = MagicMock()

    with pytest.raises(PolygonStepError, match="REPORT") as error:
        run_and_record(upload_test_script.STEP, PROBLEM_ID, flat_spec, linked_problem, client)

    assert SKIP_CHECK_ENV in str(error.value)
    client.call.assert_not_called()


@pytest.mark.parametrize("exc", [ScriptCheckUnavailable("no java"), ScriptRenderError("bad")])
def test_upload_continues_with_warning_when_check_fails(
    monkeypatch, linked_problem, flat_spec, caplog, exc
):
    monkeypatch.delenv(SKIP_CHECK_ENV)

    def fail(*args, **kwargs):
        raise exc

    monkeypatch.setattr(upload_test_script, "check_script", fail)
    client = MagicMock()

    with caplog.at_level(logging.WARNING):
        run_and_record(upload_test_script.STEP, PROBLEM_ID, flat_spec, linked_problem, client)

    client.call.assert_called_once()
    assert str(exc) in caplog.text


def test_skip_env_disables_check(monkeypatch, linked_problem, flat_spec):
    monkeypatch.setattr(upload_test_script, "check_script", lambda *a, **k: _duplicates_result())
    client = MagicMock()

    run_and_record(upload_test_script.STEP, PROBLEM_ID, flat_spec, linked_problem, client)

    client.call.assert_called_once()


# --- polygon check ----------------------------------------------------------


def _check_cli(tmp_path: Path) -> int:
    return cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "polygon", "check"])


def test_polygon_check_reports_duplicates(tmp_path, capsys):
    _jar_or_skip()
    problem_dir = tmp_path / "problems" / PROBLEM_ID
    problem_dir.mkdir(parents=True)
    (problem_dir / "test_script").write_text(DUPLICATES_SCRIPT, encoding="utf-8")
    (problem_dir / "test_script_groups").write_text("gen -n=1 > $\n", encoding="utf-8")

    assert _check_cli(tmp_path) == 1
    out = capsys.readouterr().out
    assert '"gen -n=100 -c=100 > $" — 2 times' in out
    assert "test_script_groups: ok" in out


def test_polygon_check_without_script(tmp_path, capsys):
    (tmp_path / "problems" / PROBLEM_ID).mkdir(parents=True)
    assert _check_cli(tmp_path) == 1
    assert "no test script" in capsys.readouterr().err
