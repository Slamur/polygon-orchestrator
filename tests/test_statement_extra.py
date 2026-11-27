import pytest

from orchestrator.resources import STATEMENT_RESOURCES_DIR
from orchestrator.statement_extra import (
    EXTRA_BEGIN,
    EXTRA_END,
    ensure_extra_resources,
    extra_resources,
    uses_extra,
)

EXTRA_CONTENT = b"\\def\\Extra#1{}%\n"
DEFAULT_PROBLEM_TEX = (STATEMENT_RESOURCES_DIR / "problem.tex").read_bytes()


@pytest.fixture
def templates_dir(tmp_path):
    path = tmp_path / "templates"
    path.mkdir()
    (path / "extra.tex").write_bytes(EXTRA_CONTENT)
    return path


@pytest.fixture
def problem_dir(tmp_path):
    path = tmp_path / "problems" / "example-problem"
    path.mkdir(parents=True)
    return path


def _statement(problem_dir, text: str, *, code: str = "ru", name: str = "legend.tex"):
    path = problem_dir / "statement" / code / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _own(problem_dir, name: str, content: bytes):
    path = problem_dir / "resources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.parametrize(
    "text",
    [
        "First.\n\n\\Extra{text}\n\nSecond.\n",
        "In the middle of\\Extra{text} a sentence.\n",
    ],
)
def test_uses_extra(problem_dir, text):
    _statement(problem_dir, text)
    assert uses_extra(problem_dir)


@pytest.mark.parametrize(
    "text",
    [
        "No command.\n",
        "A longer name \\Extras{text}.\n",
        "Commented out: % \\Extra{text}\n",
    ],
)
def test_does_not_use_extra(problem_dir, text):
    _statement(problem_dir, text)
    assert not uses_extra(problem_dir)


def test_escaped_percent_is_not_a_comment(problem_dir):
    _statement(problem_dir, "50\\% \\Extra{text}\n")
    assert uses_extra(problem_dir)


def test_any_language_and_section(problem_dir):
    _statement(problem_dir, "Text.\n")
    _statement(problem_dir, "\\Extra{text}\n", code="en", name="notes.tex")
    assert uses_extra(problem_dir)


def test_no_statement(problem_dir, templates_dir):
    assert extra_resources(problem_dir, templates_dir=templates_dir) == {}


def test_without_extra_nothing_is_written(problem_dir, templates_dir):
    _statement(problem_dir, "Text.\n")
    assert ensure_extra_resources(problem_dir, templates_dir=templates_dir) == []
    assert not (problem_dir / "resources").exists()


def _block(newline: str = "\n") -> str:
    return newline.join([EXTRA_BEGIN, EXTRA_CONTENT.decode().rstrip("\n"), EXTRA_END]) + newline


def test_default_problem_tex_gets_the_block(problem_dir, templates_dir):
    _statement(problem_dir, "\\Extra{text}\n")

    assert ensure_extra_resources(problem_dir, templates_dir=templates_dir) == ["problem.tex"]

    resources = problem_dir / "resources"
    assert sorted(path.name for path in resources.iterdir()) == ["problem.tex"]
    assert (resources / "problem.tex").read_bytes() == _block().encode() + DEFAULT_PROBLEM_TEX
    # the second run changes nothing
    assert extra_resources(problem_dir, templates_dir=templates_dir) == {}


def test_own_problem_tex_gets_the_block_with_its_line_breaks(problem_dir, templates_dir):
    _statement(problem_dir, "\\Extra{text}\n")
    _own(problem_dir, "problem.tex", b"own\r\ntemplate\r\n")

    files = extra_resources(problem_dir, templates_dir=templates_dir)

    assert files["problem.tex"] == _block("\r\n").encode() + b"own\r\ntemplate\r\n"


def test_outdated_block_is_replaced(problem_dir, templates_dir):
    _statement(problem_dir, "\\Extra{text}\n")
    old = f"% before\n{EXTRA_BEGIN}\n% old definition\n{EXTRA_END}\nown\n"
    _own(problem_dir, "problem.tex", old.encode())

    files = extra_resources(problem_dir, templates_dir=templates_dir)

    assert files["problem.tex"] == b"% before\n" + _block().encode() + b"own\n"


def test_current_block_is_kept(problem_dir, templates_dir):
    _statement(problem_dir, "\\Extra{text}\n")
    _own(problem_dir, "problem.tex", _block().encode() + b"own\n")

    assert extra_resources(problem_dir, templates_dir=templates_dir) == {}


def test_missing_template(problem_dir, tmp_path):
    _statement(problem_dir, "\\Extra{text}\n")
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(FileNotFoundError, match="extra.tex"):
        extra_resources(problem_dir, templates_dir=empty)


def test_template_is_ascii_and_defines_the_command():
    from orchestrator.statement_extra import EXTRA_TEMPLATES_DIR

    content = (EXTRA_TEMPLATES_DIR / "extra.tex").read_bytes()
    content.decode("ascii")
    assert b"\\def\\Extra#1" in content
    # its helpers' names have `@`: the file is input inside the document
    assert content.index(b"\\makeatletter") < content.index(b"@") < content.rindex(b"\\makeatother")
