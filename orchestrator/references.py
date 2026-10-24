"""Референсы спека — файлы других задач, которые прикладываются к вызову
модели как образцы (см. docs/SPEC_FORMAT.md, "references").

Референс — путь от корня репозитория на файл или каталог. Каталог-референс
устроен как результат оркестратора `problems/<problem_id>/` (`constraints.yaml`,
`validator.cpp`, `generators/`, ...) и, возможно, со своим спеком `spec.yaml`
рядом — так устроены учебные задачи `examples/<problem_id>/`, но
подходит и каталог прошлой задачи автора в `problems/` (в том числе
выгруженной `polygon pull`). Каждый шаг берёт из каталога только свою часть
(`REFERENCE_DOCUMENTS`); файл-референс прикладывается как есть.

Референсы бывают общими (`references` в корне спека — ко всем шагам) и
шаговыми (`references` в секции шага, `STEP_SECTIONS`) — шаговые
добавляются к общим, а не заменяют их.

Ошибки (`SpecReferenceError`) — до вызова модели, чтобы опечатка в пути не
превратилась в молча пропущенный образец:
- путь не существует;
- шаговый каталог-референс не дал этому шагу ни одного файла;
- общий каталог-референс не дал ни одного файла ни одному шагу.
Общий каталог, в котором нет части для конкретного шага (например, у
задачи со стандартным чекером нет `checker.cpp`), для этого шага просто
пропускается.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from orchestrator.spec import SPEC_FILENAME, ProblemSpec

# Что каждый шаг берёт из каталога-референса: пути относительно каталога,
# запись с `*` — glob (файлы в отсортированном порядке). `spec.yaml`
# прикладывается, только если из каталога взят хотя бы один другой файл —
# спек без результата образцом не служит.
REFERENCE_DOCUMENTS: dict[str, list[str]] = {
    "statement_draft": [SPEC_FILENAME, "statement/*.tex"],
    "constraints_pick": [SPEC_FILENAME, "constraints.yaml", "validator.cpp"],
    "generators_and_script": [
        SPEC_FILENAME,
        "constraints.yaml",
        "generators/*.cpp",
        "test_script",
        "test_script_groups",
    ],
    "solutions_draft": [SPEC_FILENAME, "solutions/*"],
    "checker_draft": [SPEC_FILENAME, "checker.cpp"],
}

# Секция спека, в которой лежат шаговые `references` для шага.
STEP_SECTIONS: dict[str, str] = {
    "statement_draft": "statement_draft",
    "constraints_pick": "constraints",
    "generators_and_script": "generation",
    "solutions_draft": "solutions",
    "checker_draft": "checker",
}


class SpecReferenceError(ValueError):
    """Референс спека не найден или ничего не даёт шагу."""


def step_references(step_name: str, spec: ProblemSpec) -> tuple[list[str], list[str]]:
    """(общие, шаговые) референсы для шага `step_name`, как записаны в спеке."""
    section = getattr(spec, STEP_SECTIONS[step_name], None)
    section_refs = (section.references if section is not None else None) or []
    return list(spec.references or []), list(section_refs)


def reference_documents(
    step_name: str,
    spec: ProblemSpec,
    *,
    keep: Optional[Callable[[str], bool]] = None,
) -> Optional[list[str]]:
    """Пути файлов, которые референсы спека дают шагу `step_name`, — сначала
    общие референсы, затем шаговые, в порядке записи в спеке; `None`, если
    ничего нет (шаг работает без образцов).

    `keep` — дополнительный фильтр по пути внутри каталога-референса
    (например, решения только на заказанных языках); на файлы-референсы и
    `spec.yaml` он не действует.
    """
    common_refs, section_refs = step_references(step_name, spec)
    paths: list[str] = []

    for ref in common_refs:
        ref_path = _existing(ref, "references")
        if ref_path.is_dir():
            if not any(_directory_files(ref_path, step) for step in REFERENCE_DOCUMENTS):
                raise SpecReferenceError(
                    f"references: directory '{ref}' has none of the files any step "
                    "takes from a reference (see REFERENCE_DOCUMENTS in "
                    "orchestrator/references.py)"
                )
            paths.extend(_directory_files(ref_path, step_name, keep))
        else:
            paths.append(str(ref_path))

    section_name = STEP_SECTIONS[step_name]
    for ref in section_refs:
        ref_path = _existing(ref, f"{section_name}.references")
        if ref_path.is_dir():
            files = _directory_files(ref_path, step_name, keep)
            if not files:
                raise SpecReferenceError(
                    f"{section_name}.references: directory '{ref}' has no files for "
                    f"step '{step_name}' (expected any of "
                    f"{REFERENCE_DOCUMENTS[step_name][1:]})"
                )
            paths.extend(files)
        else:
            paths.append(str(ref_path))

    return paths or None


def _existing(ref: str, field: str) -> Path:
    path = Path(ref)
    if not path.exists():
        raise SpecReferenceError(f"{field}: '{ref}' not found (paths are relative to the repository root)")
    return path


def _directory_files(
    directory: Path, step_name: str, keep: Optional[Callable[[str], bool]] = None
) -> list[str]:
    """Файлы каталога-референса для шага: по `REFERENCE_DOCUMENTS[step_name]`,
    `spec.yaml` — только если есть что-то кроме него."""
    spec_file: Optional[Path] = None
    files: list[Path] = []
    for entry in REFERENCE_DOCUMENTS[step_name]:
        if entry == SPEC_FILENAME:
            candidate = directory / SPEC_FILENAME
            spec_file = candidate if candidate.is_file() else None
            continue
        if "*" in entry:
            matches = sorted(path for path in directory.glob(entry) if path.is_file())
        else:
            matches = [directory / entry] if (directory / entry).is_file() else []
        files.extend(
            path
            for path in matches
            if keep is None or keep(path.relative_to(directory).as_posix())
        )
    if not files:
        return []
    return [str(path) for path in ([spec_file] if spec_file else []) + files]
