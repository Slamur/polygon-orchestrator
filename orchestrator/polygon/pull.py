"""Выгрузка задачи из Polygon в `outputs/<problem_id>/` —
`orchestrator polygon <problem_id> pull`.

Обратное направление к polygon-шагам (`steps/`): привязывает `problem_id` к
уже существующей на Polygon задаче (`polygon_state.json`) и скачивает те
части задачи, которыми пользуется оркестратор и которых локально НЕТ.
Существующие локальные файлы никогда не перезаписываются и не сравниваются
с Polygon — решение принимается по каждому файлу отдельно (есть
`legend.tex`, но нет `notes.tex` — скачивается только `notes.tex`).

Спек (`specs/<problem_id>.yaml`) не нужен и не читается: команда рассчитана
в том числе на задачи, у которых локально нет вообще ничего.

Что куда выгружается (имена — те же, что читают polygon-шаги):

- условие (`problem.statements`, язык `russian`) -> `statement/legend.tex`,
  `input_format.tex`, `output_format.tex`, `notes.tex`. Пустой на Polygon
  раздел выгружается пустым файлом — `upload_statement` требует все четыре;
- тесты с флагом "use in statements" (`problem.tests`, testset `tests`) ->
  `statement/examples/example_<K>.txt`, K — порядковый номер такого теста
  (1-based), а не его индекс на Polygon;
- TL/ML (`problem.info`) -> `constraints.yaml`, только секция `limits`.
  Это НЕ результат `constraints_pick`: диапазонов переменных, групп и
  секции `validator` в нём нет (см. `_CONSTRAINTS_HEADER`);
- валидатор (`problem.validator` + `problem.viewFile`) -> `validator.cpp`;
- чекер (`problem.checker`) -> `checker.cpp`, если он не стандартный
  (`std::...` — скачивать нечего);
- остальные source-файлы `*.cpp` (`problem.files`) -> `generators/`.
  Эвристика: Polygon не различает генераторы и прочие source-файлы, поэтому
  генератором считается всё, что не валидатор и не чекер;
- test-script (`problem.script`, testset `tests`) -> `test_script`, если
  локально нет ни `test_script`, ни `test_script_groups`;
- решения (`problem.solutions` + `problem.viewSolution`) -> `solutions/`.
  Тег решения на Polygon локально не сохраняется: `upload_solutions` выводит
  тег из имени файла, поэтому про имена не по его схеме — предупреждение.

`templates/problem_lib.h` не выгружается — это общий шаблон, а не артефакт
задачи. Записи шагов в `polygon_state.json` (`steps`) не создаются: они
означают "что шаг последним отправил", а `pull` ничего не отправляет —
`polygon status` для выгруженных частей остаётся "not run".

Ошибка Polygon API при выгрузке одной части не останавливает остальные.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orchestrator.cache import OUTPUTS_DIR
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import _parse_solution_filename

logger = logging.getLogger(__name__)

# Статусы PullItem.status:
ITEM_DOWNLOADED = "downloaded"
ITEM_KEPT = "kept"  # локальный файл уже есть — не тронут
ITEM_ABSENT = "absent"  # на Polygon этой части нет — скачивать нечего
ITEM_ERROR = "error"

_LANG = "russian"
_TESTSET = "tests"

# Поле объекта Statement -> файл в `statement/` (обратное к
# `upload_statement._TEX_PARAM_NAMES`).
_STATEMENT_FILES = {
    "legend": "legend.tex",
    "input": "input_format.tex",
    "output": "output_format.tex",
    "notes": "notes.tex",
}

_VALIDATOR_FILENAME = "validator.cpp"
_CHECKER_FILENAME = "checker.cpp"
_CONSTRAINTS_FILENAME = "constraints.yaml"
_SCRIPT_FILENAMES = ("test_script", "test_script_groups")
_STANDARD_CHECKER_PREFIX = "std::"

_CONSTRAINTS_HEADER = (
    "# Pulled from Polygon (orchestrator polygon ... pull): TL/ML only.\n"
    "# This is NOT a constraints_pick result — there are no variable ranges,\n"
    "# test groups or validator section here. Steps that take constraints.yaml\n"
    "# as input (generators_and_script, solutions_draft) will see only this.\n"
)


@dataclass
class PullItem:
    """Что произошло с одной частью задачи (обычно — с одним файлом)."""

    name: str  # путь относительно `outputs/<problem_id>/`
    status: str
    detail: str = ""


@dataclass
class PullResult:
    problem_id: str
    polygon_id: int
    link_detail: str
    items: list[PullItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.status != ITEM_ERROR for item in self.items)


def pull_problem(
    problem_id: str,
    *,
    polygon_id: int | None = None,
    outputs_dir: Path = OUTPUTS_DIR,
    client: PolygonClient | None = None,
    on_item: Callable[[PullItem], None] | None = None,
) -> PullResult:
    """Привязывает `problem_id` к задаче на Polygon и скачивает недостающие
    локально части (см. докстринг модуля).

    Какая задача на Polygon: уже записанная в `polygon_state.json`; иначе
    `polygon_id`, если передан; иначе поиск по имени `problem_id`. Если
    задачу определить не удалось — `PolygonStepError`, на диск ничего не
    пишется. `PolygonApiError` на этом этапе тоже пробрасывается наружу.

    `on_item` вызывается после каждой части — чтобы CLI показывал прогресс
    по мере выполнения.
    """
    client = client or PolygonClient()
    resolved_id, link_detail, warnings = _resolve_and_link(
        problem_id, polygon_id, Path(outputs_dir), client
    )
    logger.info("[pull] %s", link_detail)
    for warning in warnings:
        logger.warning("[pull] %s", warning)

    result = PullResult(
        problem_id=problem_id, polygon_id=resolved_id, link_detail=link_detail, warnings=warnings
    )
    puller = _Puller(
        client=client,
        polygon_id=resolved_id,
        problem_dir=Path(outputs_dir) / problem_id,
        result=result,
        on_item=on_item,
    )
    for name, part in (
        ("statement", puller.pull_statement),
        ("statement/examples", puller.pull_examples),
        (_CONSTRAINTS_FILENAME, puller.pull_limits),
        (_VALIDATOR_FILENAME, puller.pull_validator),
        (_CHECKER_FILENAME, puller.pull_checker),
        ("generators", puller.pull_generators),
        ("test_script", puller.pull_script),
        ("solutions", puller.pull_solutions),
    ):
        try:
            part()
        except PolygonApiError as exc:
            puller.add(name, ITEM_ERROR, str(exc))
    return result


def _resolve_and_link(
    problem_id: str, polygon_id: int | None, outputs_dir: Path, client: PolygonClient
) -> tuple[int, str, list[str]]:
    """(Polygon problemId, сообщение о привязке, предупреждения). Если
    привязки ещё не было — создаёт `polygon_state.json`."""
    state = load_polygon_state(problem_id, outputs_dir=outputs_dir)
    if state is not None:
        if polygon_id is not None and polygon_id != state.polygon_id:
            raise PolygonStepError(
                f"'{problem_id}': already linked to Polygon id={state.polygon_id} "
                f"(polygon_state.json), but --polygon-id={polygon_id} was given — "
                "remove polygon_state.json to relink"
            )
        return state.polygon_id, f"already linked to Polygon id={state.polygon_id}", []

    warnings: list[str] = []
    if polygon_id is not None:
        found = [
            item
            for item in client.call("problems.list", {"id": str(polygon_id)}) or []
            if item.get("id") == polygon_id
        ]
        if not found:
            raise PolygonStepError(
                f"'{problem_id}': Polygon problem id={polygon_id} not found among the "
                "problems available to this API key"
            )
        polygon_name = found[0].get("name")
        if polygon_name != problem_id:
            warnings.append(
                f"Polygon problem id={polygon_id} is named {polygon_name!r}, not "
                f"{problem_id!r} — linked anyway, as requested by --polygon-id"
            )
        detail = f"linked to Polygon id={polygon_id} (given by --polygon-id)"
    else:
        # Фильтр `name` у `problems.list` может оказаться неточным (не
        # проверено на реальном API) — оставляем только точные совпадения.
        found = [
            item
            for item in client.call("problems.list", {"name": problem_id}) or []
            if item.get("name") == problem_id and not item.get("deleted")
        ]
        if not found:
            raise PolygonStepError(
                f"'{problem_id}': no Polygon problem with this name is available to "
                "this API key — pass the numeric id explicitly: --polygon-id N"
            )
        if len(found) > 1:
            candidates = ", ".join(f"{item['id']} (owner {item.get('owner')})" for item in found)
            raise PolygonStepError(
                f"'{problem_id}': several Polygon problems have this name: {candidates} "
                "— pick one with --polygon-id N"
            )
        polygon_id = found[0]["id"]
        detail = f"found Polygon problem by name (id={polygon_id}), linked"

    save_polygon_state(
        problem_id,
        PolygonState(
            problem_id=problem_id,
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        outputs_dir=outputs_dir,
    )
    return polygon_id, detail, warnings


@dataclass
class _Puller:
    """Выгрузка частей одной задачи; каждая `pull_*` — одна часть."""

    client: PolygonClient
    polygon_id: int
    problem_dir: Path
    result: PullResult
    on_item: Callable[[PullItem], None] | None = None

    def add(self, name: str, status: str, detail: str = "") -> None:
        item = PullItem(name, status, detail)
        self.result.items.append(item)
        level = logging.ERROR if status == ITEM_ERROR else logging.INFO
        logger.log(level, "[pull] %s: %s%s", name, status, f" — {detail}" if detail else "")
        if self.on_item is not None:
            self.on_item(item)

    def warn(self, message: str) -> None:
        self.result.warnings.append(message)
        logger.warning("[pull] %s", message)

    def pull_statement(self) -> None:
        missing = {
            field_name: filename
            for field_name, filename in _STATEMENT_FILES.items()
            if not (self.problem_dir / "statement" / filename).exists()
        }
        for filename in _STATEMENT_FILES.values():
            if filename not in missing.values():
                self.add(f"statement/{filename}", ITEM_KEPT)
        if not missing:
            return

        statements = self._call("problem.statements") or {}
        statement = statements.get(_LANG)
        for field_name, filename in missing.items():
            name = f"statement/{filename}"
            if statement is None:
                self.add(name, ITEM_ABSENT, f"no {_LANG!r} statement on Polygon")
            else:
                self._write(name, (statement.get(field_name) or "").encode("utf-8"))

    def pull_examples(self) -> None:
        tests = self._call("problem.tests", {"testset": _TESTSET}) or []
        statement_tests = sorted(
            (test for test in tests if test.get("useInStatements")),
            key=lambda test: test["index"],
        )
        if not statement_tests:
            self.add("statement/examples", ITEM_ABSENT, "no tests used in statements on Polygon")
            return

        for number, test in enumerate(statement_tests, start=1):
            name = f"statement/examples/example_{number}.txt"
            if (self.problem_dir / name).exists():
                self.add(name, ITEM_KEPT)
                continue
            try:
                # У сгенерированных (не manual) тестов поля `input` нет.
                content = (
                    test["input"].encode("utf-8")
                    if test.get("input") is not None
                    else self._call_raw(
                        "problem.testInput",
                        {"testset": _TESTSET, "testIndex": str(test["index"])},
                    )
                )
            except PolygonApiError as exc:
                self.add(name, ITEM_ERROR, str(exc))
                continue
            self._write(name, content, detail=f"Polygon test {test['index']}")

    def pull_limits(self) -> None:
        if (self.problem_dir / _CONSTRAINTS_FILENAME).exists():
            self.add(_CONSTRAINTS_FILENAME, ITEM_KEPT)
            return
        info = self._call("problem.info") or {}
        time_limit_ms, memory_limit_mb = info.get("timeLimit"), info.get("memoryLimit")
        if time_limit_ms is None or memory_limit_mb is None:
            self.add(_CONSTRAINTS_FILENAME, ITEM_ABSENT, "no timeLimit/memoryLimit on Polygon")
            return
        seconds = time_limit_ms / 1000
        content = (
            f"{_CONSTRAINTS_HEADER}"
            "limits:\n"
            f"  time_limit_seconds: {int(seconds) if seconds.is_integer() else seconds}\n"
            f"  memory_limit_mb: {memory_limit_mb}\n"
        )
        self._write(
            _CONSTRAINTS_FILENAME, content.encode("utf-8"), detail="TL/ML only, see file header"
        )

    def pull_validator(self) -> None:
        if (self.problem_dir / _VALIDATOR_FILENAME).exists():
            self.add(_VALIDATOR_FILENAME, ITEM_KEPT)
            return
        polygon_name = self._call("problem.validator")
        if not polygon_name:
            self.add(_VALIDATOR_FILENAME, ITEM_ABSENT, "no validator set on Polygon")
            return
        self._write(
            _VALIDATOR_FILENAME,
            self._view_source(polygon_name),
            detail=f"Polygon source file {polygon_name}",
        )

    def pull_checker(self) -> None:
        if (self.problem_dir / _CHECKER_FILENAME).exists():
            self.add(_CHECKER_FILENAME, ITEM_KEPT)
            return
        polygon_name = self._call("problem.checker")
        if not polygon_name:
            self.add(_CHECKER_FILENAME, ITEM_ABSENT, "no checker set on Polygon")
        elif polygon_name.startswith(_STANDARD_CHECKER_PREFIX):
            self.add(
                _CHECKER_FILENAME, ITEM_ABSENT, f"standard checker {polygon_name} — no file"
            )
        else:
            self._write(
                _CHECKER_FILENAME,
                self._view_source(polygon_name),
                detail=f"Polygon source file {polygon_name}",
            )

    def pull_generators(self) -> None:
        files = self._call("problem.files") or {}
        not_generators = {
            _VALIDATOR_FILENAME,
            _CHECKER_FILENAME,
            self._call("problem.validator"),
            self._call("problem.checker"),
        }
        names = sorted(
            file["name"]
            for file in files.get("sourceFiles") or []
            if file["name"].endswith(".cpp") and file["name"] not in not_generators
        )
        if not names:
            self.add("generators", ITEM_ABSENT, "no generator source files on Polygon")
            return
        for polygon_name in names:
            name = f"generators/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
            elif (self.problem_dir / name).exists():
                self.add(name, ITEM_KEPT)
            else:
                self._download(name, lambda: self._view_source(polygon_name))

    def pull_script(self) -> None:
        existing = [name for name in _SCRIPT_FILENAMES if (self.problem_dir / name).exists()]
        if existing:
            self.add(existing[0], ITEM_KEPT)
            return
        content = self._call_raw("problem.script", {"testset": _TESTSET})
        if not content.strip():
            self.add(_SCRIPT_FILENAMES[0], ITEM_ABSENT, "empty test script on Polygon")
            return
        self._write(_SCRIPT_FILENAMES[0], content)

    def pull_solutions(self) -> None:
        solutions = sorted(
            self._call("problem.solutions") or [], key=lambda solution: solution["name"]
        )
        if not solutions:
            self.add("solutions", ITEM_ABSENT, "no solutions on Polygon")
            return
        for solution in solutions:
            polygon_name = solution["name"]
            name = f"solutions/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
                continue
            if (self.problem_dir / name).exists():
                self.add(name, ITEM_KEPT)
                continue
            downloaded = self._download(
                name,
                lambda: self._call_raw("problem.viewSolution", {"name": polygon_name}),
                detail=f"Polygon tag {solution.get('tag')}",
            )
            if downloaded:
                self._warn_if_unparseable_solution_name(polygon_name, solution.get("tag"))

    def _warn_if_unparseable_solution_name(self, polygon_name: str, tag: object) -> None:
        try:
            _parse_solution_filename(Path(polygon_name))
        except PolygonStepError:
            self.warn(
                f"solutions/{polygon_name}: the name does not follow "
                "<verdict>_<language>_<description>_<author>.<ext>, so upload_solutions "
                f"will refuse to run until it is renamed (tag on Polygon: {tag})"
            )

    def _download(self, name: str, fetch: Callable[[], bytes], *, detail: str = "") -> bool:
        """Скачивает и пишет один файл; ошибка API — запись "error" по этому
        файлу, остальные файлы той же части продолжают выгружаться."""
        try:
            content = fetch()
        except PolygonApiError as exc:
            self.add(name, ITEM_ERROR, str(exc))
            return False
        self._write(name, content, detail=detail)
        return True

    def _write(self, name: str, content: bytes, *, detail: str = "") -> None:
        path = self.problem_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.add(name, ITEM_DOWNLOADED, detail)

    def _view_source(self, polygon_name: str) -> bytes:
        return self._call_raw("problem.viewFile", {"type": "source", "name": polygon_name})

    def _call(self, method_name: str, params: dict[str, str] | None = None):
        return self.client.call(method_name, {"problemId": str(self.polygon_id), **(params or {})})

    def _call_raw(self, method_name: str, params: dict[str, str]) -> bytes:
        return self.client.call_raw(method_name, {"problemId": str(self.polygon_id), **params})


def _is_plain_filename(name: str) -> bool:
    """Имя файла приходит с Polygon — не даём ему увести запись за пределы
    каталога части (`../x`, абсолютный путь)."""
    return bool(name) and name not in (".", "..") and Path(name).name == name and "\\" not in name
