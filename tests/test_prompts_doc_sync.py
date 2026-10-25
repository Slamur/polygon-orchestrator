"""`docs/PROMPTS.md` дублирует тексты `prompts/<step>/{system.md,user.md.j2}`
для чтения подряд. Исполняются файлы, а не документ, поэтому без проверки
копии незаметно расходятся с оригиналом — этот тест ловит расхождение.
"""

import re
from pathlib import Path

import pytest

from orchestrator.step_order import STEP_ORDER

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
PROMPTS_DOC = REPO_ROOT / "docs" / "PROMPTS.md"
PROMPT_FILES = ["system.md", "user.md.j2"]


def _doc_blocks() -> dict[tuple[str, str], str]:
    """(шаг, имя файла) -> содержимое блока кода из `docs/PROMPTS.md`."""
    blocks: dict[tuple[str, str], str] = {}
    # Делим по заголовкам шагов, а не по любому `## `: строки с `## ` есть и
    # внутри самих user.md.j2.
    headings = r"## (?:Шаг \d+|Спек по черновику)"
    for section in re.split(rf"(?m)^(?={headings})", PROMPTS_DOC.read_text(encoding="utf-8")):
        heading = re.match(headings + r"[^\n]*?`(\w+)`", section)
        if not heading:
            continue
        for filename in PROMPT_FILES:
            block = re.search(
                r"### " + re.escape(filename) + r"\n\n```\n(.*?)\n```\n", section, re.S
            )
            if block:
                blocks[(heading.group(1), filename)] = block.group(1)
    return blocks


@pytest.mark.parametrize("step_name", [*STEP_ORDER, "spec_from_draft"])
@pytest.mark.parametrize("filename", PROMPT_FILES)
def test_prompts_doc_matches_prompt_file(step_name, filename):
    blocks = _doc_blocks()
    assert (step_name, filename) in blocks, (
        f"docs/PROMPTS.md has no '{filename}' block for step '{step_name}'"
    )
    prompt_text = (PROMPTS_DIR / step_name / filename).read_text(encoding="utf-8")
    assert blocks[(step_name, filename)] == prompt_text.rstrip("\n"), (
        f"docs/PROMPTS.md is out of sync with prompts/{step_name}/{filename}"
    )


def test_prompts_doc_matches_revise_prompt():
    section = PROMPTS_DOC.read_text(encoding="utf-8").split("## Режим доработки", 1)
    assert len(section) == 2, "docs/PROMPTS.md has no 'Режим доработки' section"
    block = re.search(r"\n`````\n(.*?)\n`````\n", section[1], re.S)
    assert block, "docs/PROMPTS.md has no revise.md.j2 block in 'Режим доработки'"
    prompt_text = (PROMPTS_DIR / "revise.md.j2").read_text(encoding="utf-8")
    assert block.group(1) == prompt_text.rstrip("\n"), (
        "docs/PROMPTS.md is out of sync with prompts/revise.md.j2"
    )
