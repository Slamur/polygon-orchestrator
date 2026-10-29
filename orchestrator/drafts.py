"""Черновики задач для `orchestrator <задача> init --draft NAME`.

Черновик — текстовый файл в свободной форме (любое расширение), в котором
автор описывает задачу: сюжет, формат, идеи решения и тестов — сколько
есть. Черновики лежат в `drafts/` (рядом с `problems/` и `examples/`), на
любой глубине: подкаталоги только группируют черновики
(`drafts/icpc/my-idea.md`); скрытые файлы и каталоги — не черновики. Как и
`problems/`, каталог в `.gitignore` — в черновике та же суть задачи, что и
в спеке (CLAUDE.md, "Конфиденциальность problems/").

`--draft` принимает путь файла относительно `drafts/` целиком
(`icpc/my-idea.md`) или без расширения (`icpc/my-idea`); имя без
подкаталогов (`my-idea.md`, `my-idea`) ищется и во всех подкаталогах.
Путь, совпавший с файлом целиком, выбирается сразу; иначе имя, под которое
подходят несколько файлов, — ошибка, а не первый попавшийся.

Модуль без тяжёлых импортов: им пользуется Tab-дополнение в `cli.py`.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from orchestrator.problem_paths import DRAFT_STEM

DRAFTS_DIR = Path("drafts")

# Под каким именем (без расширения) копия черновика ложится в каталог
# задачи: `<каталог задачи>/authoring/draft<расширение черновика>`.
PROBLEM_DRAFT_STEM = DRAFT_STEM


class DraftError(ValueError):
    """Черновик не найден или имя неоднозначно."""


def draft_names(drafts_dir: Path) -> list[str]:
    """Пути файлов черновиков относительно `drafts_dir` (через `/`, на любой
    глубине; скрытые файлы и каталоги, например `.gitkeep`, — не черновики),
    отсортированы. Нет каталога — пустой список."""
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
    """Подходит ли черновик `rel` (путь из `draft_names`) под `name` из
    `--draft`: путь с расширением или без, а имя без подкаталогов — и
    в любом подкаталоге."""
    path = PurePosixPath(rel)
    candidates = {rel, path.with_suffix("").as_posix()}
    if "/" not in name:
        candidates |= {path.name, path.stem}
    return name in candidates


def find_draft(drafts_dir: Path, name: str) -> Path:
    """Файл черновика по имени с расширением или без (см. докстринг модуля);
    пустой или не текстовый черновик — тоже `DraftError`."""
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
    """Имя копии черновика в каталоге задачи (`PROBLEM_DRAFT_STEM`)."""
    return PROBLEM_DRAFT_STEM + Path(draft_path).suffix
