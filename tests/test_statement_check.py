from pathlib import Path

from orchestrator import cli
from orchestrator.checks.statement_check import (
    FIX_BACKUP_DIRNAME,
    KIND_MARKDOWN_LIST,
    KIND_QUOTES,
    KIND_UNPAIRED_QUOTES,
    check_text,
    fix_text,
)

PROBLEM_ID = "example-problem"
FILE = "statement/legend.tex"


def _kinds(text: str) -> list[tuple[int, str]]:
    return [(issue.line, issue.kind) for issue in check_text(text, FILE)]


# --- списки -------------------------------------------------------------------


def test_bullet_list_becomes_itemize():
    text = "Дано:\n\n- первое;\n\n- второе.\n\nДальше текст.\n"
    assert _kinds(text) == [(3, KIND_MARKDOWN_LIST), (5, KIND_MARKDOWN_LIST)]
    assert fix_text(text) == (
        "Дано:\n\n\\begin{itemize}\n\\item первое;\n\n\\item второе.\n\\end{itemize}\n\n"
        "Дальше текст.\n"
    )


def test_numbered_list_becomes_enumerate():
    text = "1. раз\n2) два\n"
    assert fix_text(text) == "\\begin{enumerate}\n\\item раз\n\\item два\n\\end{enumerate}\n"


def test_nested_list_and_continuation_line():
    text = "- a\n  продолжение\n  - b\n- c\n"
    assert fix_text(text) == (
        "\\begin{itemize}\n\\item a\n  продолжение\n  \\begin{itemize}\n  \\item b\n"
        "  \\end{itemize}\n\\item c\n\\end{itemize}\n"
    )


def test_list_kind_change_starts_new_environment():
    text = "- a\n1. b\n"
    assert fix_text(text) == (
        "\\begin{itemize}\n\\item a\n\\end{itemize}\n"
        "\\begin{enumerate}\n\\item b\n\\end{enumerate}\n"
    )


def test_dashes_and_latex_lists_are_not_markdown():
    text = "--- тире\n$-1$\n\\begin{itemize}\n\\item a\n\\end{itemize}\n"
    assert _kinds(text) == []
    assert fix_text(text) == text


# --- кавычки ------------------------------------------------------------------


def test_quotes_become_guillemets():
    text = 'Выведите "YES", “NO”, «MAYBE» или ``X\'\'.\n'
    assert _kinds(text) == [(1, KIND_QUOTES)]
    assert fix_text(text) == "Выведите <<YES>>, <<NO>>, <<MAYBE>> или <<X>>.\n"


def test_quotes_across_lines_of_paragraph():
    text = 'Строка "начало\nконец" тут.\n'
    assert fix_text(text) == "Строка <<начало\nконец>> тут.\n"


def test_nested_quotes():
    assert fix_text("«внешняя „внутренняя“ цитата»") == "<<внешняя <<внутренняя>> цитата>>"


def test_quotes_in_math_comments_and_escapes_are_kept():
    text = '$a"b$ и \\(x"\\) и \\"o % "комментарий\n'
    assert _kinds(text) == []
    assert fix_text(text) == text


def test_unpaired_quotes_are_reported_and_not_fixed():
    text = 'Один " без пары.\n\nА "тут" — пара.\n'
    issues = check_text(text, FILE)
    assert [(issue.line, issue.kind, issue.fixable) for issue in issues] == [
        (1, KIND_UNPAIRED_QUOTES, False),
        (3, KIND_QUOTES, True),
    ]
    assert fix_text(text) == 'Один " без пары.\n\nА <<тут>> — пара.\n'


def test_guillemets_are_ok():
    assert _kinds("Ответ <<YES>>.\n") == []


# --- local check / local fix --------------------------------------------------


def _cli(tmp_path: Path, command: str) -> int:
    return cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", command])


def _statement(tmp_path: Path, files: dict[str, str]) -> Path:
    statement_dir = tmp_path / "problems" / PROBLEM_ID / "statement"
    statement_dir.mkdir(parents=True)
    for name, text in files.items():
        (statement_dir / name).write_text(text, encoding="utf-8")
    return statement_dir


def test_local_check_reports_statement_issues(tmp_path, capsys):
    _statement(tmp_path, {"legend.tex": '- пункт "a"\n', "notes.tex": "Ответ <<YES>>.\n"})

    assert _cli(tmp_path, "check") == 1
    out = capsys.readouterr().out
    assert "statement/legend.tex:1: markdown list" in out
    assert "statement/legend.tex:1: quotes" in out
    assert "notes.tex" not in out
    assert "local fix" in out
    assert "no test script" in out


def test_local_check_ok_statement(tmp_path, capsys):
    _statement(tmp_path, {"legend.tex": "Ответ <<YES>>.\n"})

    assert _cli(tmp_path, "check") == 0
    assert "statement: ok" in capsys.readouterr().out


def test_local_fix_rewrites_files_with_backup(tmp_path, capsys):
    statement_dir = _statement(
        tmp_path, {"legend.tex": '- "a"\n', "notes.tex": "Ответ <<YES>>.\n"}
    )

    assert _cli(tmp_path, "fix") == 0
    assert (statement_dir / "legend.tex").read_text(encoding="utf-8") == (
        "\\begin{itemize}\n\\item <<a>>\n\\end{itemize}\n"
    )
    backups = list((statement_dir.parent / FIX_BACKUP_DIRNAME).glob("*/statement/*.tex"))
    assert [path.name for path in backups] == ["legend.tex"]
    assert backups[0].read_text(encoding="utf-8") == '- "a"\n'
    assert "legend.tex: fixed" in capsys.readouterr().out
    assert _cli(tmp_path, "check") == 0


def test_local_fix_reports_unpaired_quotes(tmp_path, capsys):
    statement_dir = _statement(tmp_path, {"legend.tex": 'Один " без пары.\n'})

    assert _cli(tmp_path, "fix") == 1
    assert (statement_dir / "legend.tex").read_text(encoding="utf-8") == 'Один " без пары.\n'
    assert "unpaired quotes" in capsys.readouterr().out


def test_local_fix_without_statement(tmp_path, capsys):
    (tmp_path / "problems" / PROBLEM_ID).mkdir(parents=True)
    assert _cli(tmp_path, "fix") == 1
    assert "no statement" in capsys.readouterr().err
