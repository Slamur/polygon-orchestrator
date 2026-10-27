"""Черновики задач для `orchestrator <задача> init --draft NAME`.

Черновик — текстовый файл в свободной форме (любое расширение), в котором
автор описывает задачу: сюжет, формат, идеи решения и тестов — сколько
есть. Черновики лежат плоско в `drafts/` (рядом с `problems/` и
`examples/`); как и `problems/`, каталог в `.gitignore` — в черновике та
же суть задачи, что и в спеке (CLAUDE.md, "Конфиденциальность problems/").

`--draft` принимает имя файла целиком (`my-idea.md`) или без расширения
(`my-idea`); имя без расширения, под которое подходят несколько файлов, —
ошибка, а не первый попавшийся.

Модуль без тяжёлых импортов: им пользуется Tab-дополнение в `cli.py`.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.problem_paths import DRAFT_STEM

DRAFTS_DIR = Path("drafts")

# Под каким именем (без расширения) копия черновика ложится в каталог
# задачи: `<каталог задачи>/authoring/draft<расширение черновика>`.
PROBLEM_DRAFT_STEM = DRAFT_STEM


class DraftError(ValueError):
    """Черновик не найден или имя неоднозначно."""


def draft_names(drafts_dir: Path) -> list[str]:
    """Имена файлов черновиков в `drafts_dir` (скрытые, например `.gitkeep`,
    — не черновики), отсортированы. Нет каталога — пустой список."""
    drafts_dir = Path(drafts_dir)
    if not drafts_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in drafts_dir.iterdir()
        if path.is_file() and not path.name.startswith(".")
    )


def find_draft(drafts_dir: Path, name: str) -> Path:
    """Файл черновика по имени с расширением или без (см. докстринг модуля);
    пустой или не текстовый черновик — тоже `DraftError`."""
    drafts_dir = Path(drafts_dir)
    names = draft_names(drafts_dir)
    matches = [name] if name in names else [n for n in names if Path(n).stem == name]
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
            "pass the file name with its extension"
        )
    available = ", ".join(names) if names else "none"
    raise DraftError(f"draft '{name}' not found in {drafts_dir} (available: {available})")


def problem_draft_name(draft_path: Path) -> str:
    """Имя копии черновика в каталоге задачи (`PROBLEM_DRAFT_STEM`)."""
    return PROBLEM_DRAFT_STEM + Path(draft_path).suffix
