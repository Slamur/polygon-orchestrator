"""Выгрузка задачи из Polygon в `problems/<problem_id>/` —
`orchestrator <задача> polygon pull [--overwrite]`.

Обратное направление к polygon-шагам (`steps/`): привязывает `problem_id` к
уже существующей на Polygon задаче (`polygon_state.json`), сравнивает с
Polygon каждую часть задачи, которой пользуется оркестратор, и
переписывает базу синхронизации (`revision`/`synced`, см.
`orchestrator/polygon/sync.py`) по тому, что сейчас на Polygon. По
аналогии с git: без `--overwrite` — `git fetch` (локальные файлы не
трогаются, кроме отсутствующих), с `--overwrite` — `git fetch && git reset
--hard`.

Решение принимается по каждому файлу отдельно:

- локально файла нет — скачивается (`downloaded`);
- совпадает с Polygon — не трогается (`up to date`);
- отличается — без `--overwrite` остаётся локальная версия (`kept`), и
  следующий `polygon push` загрузит её на Polygon (в базу записывается хеш
  версии с Polygon — push сравнивает с ним локальный); с `--overwrite`
  локальный файл переносится в `.pull_backup/<время>/` (`problems/` вне git,
  другой копии у него нет) и заменяется версией с Polygon (`overwritten`);
- есть только локально — не трогается (`local only`), и следующий push
  загрузит его; локальные файлы pull никогда не удаляет.

Pull отказывается работать, если в рабочей копии задачи на Polygon есть
незакоммиченные правки: база синхронизации — всегда закоммиченная ревизия.

Файлы, которые Polygon отдаёт листингом с временем изменения (валидатор,
нестандартный чекер, генераторы, решения, `problem_lib.h`, `spec.yaml`),
скачиваются, только если версия на Polygon может отличаться от записанной в
базе. Версия на Polygon считается известной — её хеш равен `synced[ключ]`,
— если у ключа есть хеш в базе и либо отметка (`stamps`, см.
`orchestrator/polygon/sync.py`) совпадает с листингом, либо ревизия на
Polygon равна записанной в базе (с последней синхронизации на Polygon ничего
не коммитили; ревизии на Polygon только растут, поэтому ключи, сверенные
даже упавшим pull, относятся к той же ревизии). Для известной версии
локальный файл сравнивается по хешу без скачивания: совпал — `up to date`,
отличается — `kept`, и скачивается он только с `--overwrite`. Отметки
записываются вместе с хешами по каждому файлу — частично упавший pull
оставляет базу, в которой каждая пара "хеш + отметка" верна. Условие,
примеры, TL/ML и test-script запрашиваются каждый раз — по одному запросу
на часть, времени изменения у них нет.

Локальный спек (`problems/<problem_id>/spec.yaml`) не нужен: команда рассчитана
в том числе на задачи, у которых локально нет вообще ничего. Сам спек
сравнивается с Polygon как обычный файл (см. ниже), но не разбирается и не
валидируется.

Что с чем сравнивается (имена — те же, что читают polygon-шаги):

- условие (`problem.statements`, язык `russian`) -> `statement/legend.tex`,
  `input_format.tex`, `output_format.tex`, `notes.tex`. Пустой на Polygon
  раздел выгружается пустым файлом — `upload_statement` требует все четыре.
  Название задачи из условия -> `statement/name.txt` (сравнивается без
  окружающих пробелов и переводов строк);
- тесты с флагом "use in statements" (`problem.tests`, testset `tests`) ->
  `statement/examples/example_<K>.txt`, K — порядковый номер такого теста
  (1-based), а не его индекс на Polygon;
- TL/ML (`problem.info`) -> `constraints.yaml`, только секция `limits`.
  Это НЕ результат `constraints_pick`: диапазонов переменных, групп и
  секции `validator` в нём нет (см. `_CONSTRAINTS_HEADER`). Сравниваются
  только TL/ML. Результат `constraints_pick` целиком не перезаписывается
  никогда: с `--overwrite` в нём заменяются только значения
  `time_limit_seconds`/`memory_limit_mb` (`_replace_limits`), остальное —
  диапазоны, группы, `validator`, комментарии — остаётся как было.
  Обоснование TL/ML (`limits.rationale`) при этом не правится;
- валидатор (`problem.validator` + `problem.viewFile`) -> `validator.cpp`;
- чекер (`problem.checker`) -> `checker.cpp`, если он не стандартный
  (`std::...` — скачивать нечего);
- остальные source-файлы `*.cpp` (`problem.files`) -> `generators/`
  (`generator_source_names`: всё, что не валидатор и не чекер);
- test-script (`problem.script`, testset `tests`) -> `test_script` (или уже
  существующий локально `test_script_groups`); сравнивается без пустых строк
  (`normalize_script`) — Polygon выкидывает их при сохранении;
- решения (`problem.solutions` + `problem.viewSolution`) -> `solutions/`.
  Тег решения на Polygon локально не сохраняется: `upload_solutions` выводит
  тег из имени файла, поэтому про имена не по его схеме — предупреждение;
- resource `problem_lib.h` -> `problem_lib.h` (копия задачи, см.
  `PROBLEM_LIB_FILENAME` в `orchestrator/steps/base.py`; если её нет на
  Polygon, push загрузит локальную, а без неё — шаблон);
- aux-файл `spec.yaml` (раздел "Attachments", `problem.files` +
  `problem.viewFile`) -> `spec.yaml` (загружает шаг `upload_spec`).

Текст сравнивается с точностью до переводов строк (`\\r\\n` = `\\n`).
NOTE: не проверено на реальном API, не нормализует ли Polygon содержимое
как-то ещё (например, концевой перевод строки) — если да, такие файлы
будут всегда `kept`, и push будет загружать их каждый раз.

Ошибка Polygon API при выгрузке одной части не останавливает остальные, но
тогда ревизия в базе не обновляется (`PullResult.ok` ложно).
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
    update_sync,
)
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.set_constraints import (
    _MEMORY_KEY,
    _TIME_KEY,
    _extract_limits,
    read_limits,
)
from orchestrator.polygon.steps.upload_solutions import _parse_solution_filename
from orchestrator.polygon.steps.upload_statement import NAME_FILENAME
from orchestrator.polygon.steps.upload_test_script import SCRIPT_FILENAMES, normalize_script
from orchestrator.polygon.sync import (
    CHECKER_FILE_KEY,
    CHECKER_KEY,
    LIMITS_KEY,
    PROBLEM_LIB_KEY,
    SCRIPT_KEY,
    SPEC_KEY,
    STATEMENT_NAME_KEY,
    STANDARD_CHECKER_PREFIX,
    VALIDATOR_FILE_KEY,
    VALIDATOR_KEY,
    digest,
    fetch_revision,
    generator_source_names,
    limits_digest,
    remote_stamps,
)
from orchestrator.problem_paths import (
    PROBLEMS_DIR,
    SPEC_FILENAME,
    ProblemPathError,
    check_new_problem_location,
    is_removed_mark,
    problem_id_for,
)
from orchestrator.steps.base import PROBLEM_LIB_FILENAME

logger = logging.getLogger(__name__)

# Статусы PullItem.status:
ITEM_DOWNLOADED = "downloaded"  # локально не было — скачан
ITEM_SAME = "up to date"  # совпадает с Polygon — не тронут
ITEM_OVERWRITTEN = "overwritten"  # отличался, --overwrite: заменён, старый — в бэкапе
ITEM_KEPT = "kept"  # отличается от Polygon, оставлена локальная версия
ITEM_LOCAL_ONLY = "local only"  # есть только локально — не тронут
ITEM_ABSENT = "absent"  # нет ни на Polygon, ни локально
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

_VALIDATOR_FILENAME = VALIDATOR_FILE_KEY
_CHECKER_FILENAME = CHECKER_FILE_KEY
_CONSTRAINTS_FILENAME = "constraints.yaml"
_BACKUP_DIRNAME = ".pull_backup"

_KEPT_DETAIL = "differs from Polygon, local version kept — the next push will upload it"
_LOCAL_ONLY_DETAIL = "not on Polygon — the next push will upload it"
_NOT_DOWNLOADED_DETAIL = "unchanged on Polygon since the last sync, not downloaded"

_CONSTRAINTS_HEADER = (
    "# Pulled from Polygon (orchestrator <problem> polygon pull): TL/ML only.\n"
    "# This is NOT a constraints_pick result — there are no variable ranges,\n"
    "# test groups or validator section here. Steps that take constraints.yaml\n"
    "# as input (generators_and_script, solutions_draft) will see only this.\n"
)


@dataclass
class PullItem:
    """Что произошло с одной частью задачи (обычно — с одним файлом)."""

    name: str  # путь относительно `problems/<problem_id>/`
    status: str
    detail: str = ""


@dataclass
class PullResult:
    problem_id: str
    polygon_id: int
    link_detail: str
    revision: int | None = None
    backup_dir: Path | None = None  # куда перенесены перезаписанные файлы, если были
    items: list[PullItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.status != ITEM_ERROR for item in self.items)


def pull_problem(
    problem_id: str,
    *,
    polygon_id: int | None = None,
    overwrite: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    on_item: Callable[[PullItem], None] | None = None,
) -> PullResult:
    """Привязывает `problem_id` к задаче на Polygon, сравнивает с ней
    локальные файлы и переписывает базу синхронизации (см. докстринг
    модуля).

    `problem_id` — путь каталога задачи внутри корня `problems_dir` (см.
    `orchestrator/problem_paths.py`). Какая задача на Polygon: уже
    записанная в `polygon_state.json`; иначе поиск неудалённой задачи с
    именем — `problem_id`, выведенным из пути (`problem_id_for`), — и id
    `polygon_id`, если он передан (на случай нескольких задач с одним
    именем). Непривязанная задача заводится только там, где её разрешает
    `check_new_problem_location`. Если
    задачу определить не удалось или в её рабочей копии есть
    незакоммиченные правки — `PolygonStepError`, файлы не трогаются.
    `PolygonApiError` на этом этапе тоже пробрасывается наружу.

    `on_item` вызывается после каждой части — чтобы CLI показывал прогресс
    по мере выполнения.
    """
    client = client or PolygonClient()
    resolved_id, link_detail = _resolve_and_link(
        problem_id, polygon_id, Path(problems_dir), client
    )
    logger.info("[pull] %s", link_detail)

    current = fetch_revision(client, resolved_id)
    if current.modified:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={resolved_id} has uncommitted changes in "
            "its working copy — commit or discard them on Polygon first"
        )

    result = PullResult(
        problem_id=problem_id,
        polygon_id=resolved_id,
        link_detail=link_detail,
        revision=current.revision,
    )
    problem_dir = Path(problems_dir) / problem_id
    base = load_polygon_state(problem_id, problems_dir=problems_dir)
    puller = _Puller(
        client=client,
        polygon_id=resolved_id,
        problem_dir=problem_dir,
        overwrite=overwrite,
        backup_dir=problem_dir
        / _BACKUP_DIRNAME
        / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        result=result,
        on_item=on_item,
        base_synced=dict(base.synced),
        base_stamps=dict(base.stamps),
        revision_unchanged=base.revision is not None and base.revision == current.revision,
    )
    for name, part in (
        ("statement", puller.pull_statement),
        ("statement/examples", puller.pull_examples),
        (_CONSTRAINTS_FILENAME, puller.pull_limits),
        (PROBLEM_LIB_FILENAME, puller.pull_problem_lib),
        (_VALIDATOR_FILENAME, puller.pull_validator),
        (_CHECKER_FILENAME, puller.pull_checker),
        ("generators", puller.pull_generators),
        ("test_script", puller.pull_script),
        ("solutions", puller.pull_solutions),
        (SPEC_FILENAME, puller.pull_spec),
    ):
        try:
            part()
        except PolygonApiError as exc:
            puller.add(name, ITEM_ERROR, str(exc))

    # Базу по успешно сравненным частям записываем всегда, ревизию — только
    # если сравнились все: иначе push не должен считать базу полной.
    update_sync(
        problem_id,
        synced=puller.synced,
        stamps=puller.stamps,
        revision=current.revision if result.ok else base.revision,
        problems_dir=problems_dir,
    )
    if puller.backed_up:
        result.backup_dir = puller.backup_dir
    return result


def _resolve_and_link(
    problem_id: str, polygon_id: int | None, problems_dir: Path, client: PolygonClient
) -> tuple[int, str]:
    """(Polygon problemId, сообщение о привязке). Если привязки ещё не было —
    создаёт `polygon_state.json`."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is not None:
        if polygon_id is not None and polygon_id != state.polygon_id:
            raise PolygonStepError(
                f"'{problem_id}': already linked to Polygon id={state.polygon_id} "
                f"(polygon_state.json), but --polygon-id={polygon_id} was given — "
                "remove polygon_state.json to relink"
            )
        return state.polygon_id, f"already linked to Polygon id={state.polygon_id}"

    try:
        check_new_problem_location(problems_dir, problem_id)
    except ProblemPathError as exc:
        raise PolygonStepError(str(exc)) from exc
    name = problem_id_for(problems_dir, problem_id)

    # Имя передаётся всегда, числовой id — если задан: `--polygon-id` только
    # сужает поиск, а не отменяет проверку имени. Фильтр `name` у
    # `problems.list` может оказаться неточным (не проверено на реальном
    # API), а удалённые задачи нам не нужны — оставляем только точные
    # совпадения среди неудалённых.
    params = {"name": name, "showDeleted": "false"}
    if polygon_id is not None:
        params["id"] = str(polygon_id)
    searched = f"named {name!r}" + (f" with id={polygon_id}" if polygon_id else "")
    found = [
        item
        for item in client.call("problems.list", params) or []
        if item.get("name") == name
        and not item.get("deleted")
        and polygon_id in (None, item.get("id"))
    ]
    if not found:
        raise PolygonStepError(
            f"'{problem_id}': no Polygon problem {searched} is available to this API key"
        )
    if len(found) > 1:
        candidates = ", ".join(f"{item['id']} (owner {item.get('owner')})" for item in found)
        raise PolygonStepError(
            f"'{problem_id}': several Polygon problems are {searched}: {candidates} "
            "— pick one with --polygon-id N"
        )
    polygon_id = found[0]["id"]
    detail = f"found Polygon problem {searched} (id={polygon_id}), linked"

    save_polygon_state(
        problem_id,
        PolygonState(
            problem_id=name,
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
        problems_dir=problems_dir,
    )
    return polygon_id, detail


@dataclass
class _Puller:
    """Сравнение частей одной задачи; каждая `pull_*` — одна часть.

    `synced` копит изменения базы синхронизации: ключ -> sha256 версии на
    Polygon (с точностью до нормализации — того, что легло бы локально), или
    `None` — версия на Polygon неизвестна или её нет, push должен загрузить.
    `stamps` — отметки версий на Polygon для тех же ключей (см.
    `update_sync`). `base_*` и `revision_unchanged` — база до этого pull,
    по ней решается, какие файлы можно не скачивать (см. докстринг модуля)."""

    client: PolygonClient
    polygon_id: int
    problem_dir: Path
    overwrite: bool
    backup_dir: Path
    result: PullResult
    on_item: Callable[[PullItem], None] | None = None
    base_synced: dict[str, str] = field(default_factory=dict)
    base_stamps: dict[str, str] = field(default_factory=dict)
    revision_unchanged: bool = False
    synced: dict[str, str | None] = field(default_factory=dict)
    stamps: dict[str, str | None] = field(default_factory=dict)
    backed_up: bool = False
    _files: dict | None = None
    _solutions: list | None = None
    _assigned: dict[str, str | None] = field(default_factory=dict)
    _remote_stamps: dict[str, str] | None = None

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
        statements = self._call("problem.statements") or {}
        statement = statements.get(_LANG)
        name_file = f"statement/{NAME_FILENAME}"
        if statement is None:
            for filename in _STATEMENT_FILES.values():
                self._missing_on_polygon(
                    f"statement/{filename}", f"no {_LANG!r} statement on Polygon"
                )
            self._missing_on_polygon(
                name_file, f"no {_LANG!r} statement on Polygon", key=STATEMENT_NAME_KEY
            )
            return
        for field_name, filename in _STATEMENT_FILES.items():
            self._sync_file(
                f"statement/{filename}",
                lambda field_name=field_name: (statement.get(field_name) or "").encode("utf-8"),
            )
        name = (statement.get("name") or "").strip()
        if not name:
            self._missing_on_polygon(
                name_file, "no problem name in the statement on Polygon", key=STATEMENT_NAME_KEY
            )
            return
        self._sync_file(
            name_file,
            lambda: f"{name}\n".encode("utf-8"),
            key=STATEMENT_NAME_KEY,
            # Как `upload_statement.read_name`: хеш и сравнение — по самому названию.
            normalize=lambda content: content.decode("utf-8").strip().encode("utf-8"),
        )

    def pull_examples(self) -> None:
        tests = self._call("problem.tests", {"testset": _TESTSET}) or []
        statement_tests = sorted(
            (test for test in tests if test.get("useInStatements")),
            key=lambda test: test["index"],
        )
        for number, test in enumerate(statement_tests, start=1):
            self._sync_file(
                f"statement/examples/example_{number}.txt",
                # У сгенерированных (не manual) тестов поля `input` нет.
                lambda test=test: (
                    test["input"].encode("utf-8")
                    if test.get("input") is not None
                    else self._call_raw(
                        "problem.testInput",
                        {"testset": _TESTSET, "testIndex": str(test["index"])},
                    )
                ),
                detail=f"Polygon test {test['index']}",
            )

        examples_dir = self.problem_dir / "statement" / "examples"
        local_extra = sorted(
            path.name
            for path in (examples_dir.glob("example_*.txt") if examples_dir.is_dir() else [])
            if path.name
            not in {f"example_{n}.txt" for n in range(1, len(statement_tests) + 1)}
        )
        for filename in local_extra:
            self._missing_on_polygon(f"statement/examples/{filename}")
        if not statement_tests and not local_extra:
            self.add("statement/examples", ITEM_ABSENT, "no tests used in statements on Polygon")

    def pull_limits(self) -> None:
        info = self._call("problem.info") or {}
        time_limit_ms, memory_limit_mb = info.get("timeLimit"), info.get("memoryLimit")
        if time_limit_ms is None or memory_limit_mb is None:
            self._missing_on_polygon(
                _CONSTRAINTS_FILENAME, "no timeLimit/memoryLimit on Polygon", key=LIMITS_KEY
            )
            return
        remote = (int(time_limit_ms), int(memory_limit_mb))
        seconds = time_limit_ms / 1000
        values = {
            _TIME_KEY: str(int(seconds) if seconds.is_integer() else seconds),
            _MEMORY_KEY: str(memory_limit_mb),
        }
        content = (
            f"{_CONSTRAINTS_HEADER}"
            "limits:\n"
            f"  {_TIME_KEY}: {values[_TIME_KEY]}\n"
            f"  {_MEMORY_KEY}: {values[_MEMORY_KEY]}\n"
        ).encode("utf-8")

        path = self.problem_dir / _CONSTRAINTS_FILENAME
        if not path.exists():
            self._write(_CONSTRAINTS_FILENAME, content)
            self.add(_CONSTRAINTS_FILENAME, ITEM_DOWNLOADED, "TL/ML only, see file header")
            self.synced[LIMITS_KEY] = limits_digest(*remote)
            return

        try:
            local = read_limits(self.problem_dir.name, self.problem_dir.parent)
        except PolygonStepError:
            local = None
        if local == remote:
            self.add(_CONSTRAINTS_FILENAME, ITEM_SAME, "TL/ML")
            self.synced[LIMITS_KEY] = limits_digest(*remote)
            return

        differs = f"TL/ML differ: local {_describe_limits(local)}, Polygon {_describe_limits(remote)}"
        is_pulled = path.read_bytes().startswith(_CONSTRAINTS_HEADER.encode("utf-8"))
        if self.overwrite:
            if is_pulled:
                replaced, detail = content, differs
            else:
                text = _replace_limits(path.read_text(encoding="utf-8"), values, remote)
                replaced = text.encode("utf-8") if text is not None else None
                detail = (
                    f"{differs} — only the limits were replaced in the constraints_pick "
                    "result, check limits.rationale"
                )
            if replaced is not None:
                self._backup(_CONSTRAINTS_FILENAME)
                self._write(_CONSTRAINTS_FILENAME, replaced)
                self.add(_CONSTRAINTS_FILENAME, ITEM_OVERWRITTEN, detail)
                self.synced[LIMITS_KEY] = limits_digest(*remote)
                return
            differs += (
                f" — could not replace {_TIME_KEY}/{_MEMORY_KEY} in constraints.yaml "
                "automatically, fix the limits in it manually"
            )
        self.add(_CONSTRAINTS_FILENAME, ITEM_KEPT, f"{differs}; {_KEPT_DETAIL}")
        self.synced[LIMITS_KEY] = None

    def pull_problem_lib(self) -> None:
        resources = {file["name"] for file in self._polygon_files().get("resourceFiles") or []}
        if PROBLEM_LIB_FILENAME not in resources:
            self._missing_on_polygon(
                PROBLEM_LIB_FILENAME,
                "no resource file on Polygon",
                key=PROBLEM_LIB_KEY,
            )
            return
        self._sync_file(
            PROBLEM_LIB_FILENAME,
            lambda: self._call_raw(
                "problem.viewFile", {"type": "resource", "name": PROBLEM_LIB_FILENAME}
            ),
            key=PROBLEM_LIB_KEY,
            stamped=True,
        )

    def pull_validator(self) -> None:
        polygon_name = self._assigned_name("problem.validator")
        if not polygon_name:
            self._missing_on_polygon(
                _VALIDATOR_FILENAME, "no validator set on Polygon", key=VALIDATOR_FILE_KEY
            )
            self.synced[VALIDATOR_KEY] = None
            return
        self._sync_file(
            _VALIDATOR_FILENAME,
            lambda: self._view_source(polygon_name),
            key=VALIDATOR_FILE_KEY,
            also={VALIDATOR_KEY: digest(polygon_name)},
            detail=f"Polygon source file {polygon_name}",
            stamped=True,
        )

    def pull_checker(self) -> None:
        polygon_name = self._assigned_name("problem.checker")
        if not polygon_name:
            self._missing_on_polygon(
                _CHECKER_FILENAME, "no checker set on Polygon", key=CHECKER_FILE_KEY
            )
            self.synced[CHECKER_KEY] = None
        elif polygon_name.startswith(STANDARD_CHECKER_PREFIX):
            self._missing_on_polygon(
                _CHECKER_FILENAME,
                f"standard checker {polygon_name} on Polygon",
                key=CHECKER_FILE_KEY,
                local_only_detail="the next push will upload it if the spec has "
                "checker.custom_needed: true",
            )
            self.synced[CHECKER_KEY] = digest(polygon_name)
        else:
            self._sync_file(
                _CHECKER_FILENAME,
                lambda: self._view_source(polygon_name),
                key=CHECKER_FILE_KEY,
                also={CHECKER_KEY: digest(polygon_name)},
                detail=f"Polygon source file {polygon_name}",
                stamped=True,
            )

    def pull_generators(self) -> None:
        names = generator_source_names(
            self._polygon_files().get("sourceFiles") or [],
            self._assigned_name("problem.validator"),
            self._assigned_name("problem.checker"),
        )
        for polygon_name in names:
            name = f"generators/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
            else:
                self._sync_file(
                    name,
                    lambda polygon_name=polygon_name: self._view_source(polygon_name),
                    stamped=True,
                )

        local_only = self._local_only("generators", set(names), pattern="*.cpp")
        if not names and not local_only:
            self.add("generators", ITEM_ABSENT, "no generator source files on Polygon")

    def pull_script(self) -> None:
        existing = [name for name in SCRIPT_FILENAMES if (self.problem_dir / name).exists()]
        local_name = existing[0] if existing else SCRIPT_FILENAMES[0]
        content = self._call_raw("problem.script", {"testset": _TESTSET})
        if not content.strip():
            self._missing_on_polygon(local_name, "empty test script on Polygon", key=SCRIPT_KEY)
            return
        # Как `upload_test_script`: пустые строки, которые выкидывает Polygon, не в счёт.
        self._sync_file(local_name, lambda: content, key=SCRIPT_KEY, normalize=normalize_script)

    def pull_solutions(self) -> None:
        solutions = sorted(self._polygon_solutions(), key=lambda solution: solution["name"])
        for solution in solutions:
            polygon_name = solution["name"]
            name = f"solutions/{polygon_name}"
            if not _is_plain_filename(polygon_name):
                self.add(name, ITEM_ERROR, "unsafe file name, skipped")
                continue
            written = self._sync_file(
                name,
                lambda polygon_name=polygon_name: self._call_raw(
                    "problem.viewSolution", {"name": polygon_name}
                ),
                detail=f"Polygon tag {solution.get('tag')}",
                stamped=True,
            )
            if written:
                self._warn_if_unparseable_solution_name(polygon_name, solution.get("tag"))

        remote_names = {solution["name"] for solution in solutions}
        local_only = self._local_only("solutions", remote_names, pattern="*")
        if not solutions and not local_only:
            self.add("solutions", ITEM_ABSENT, "no solutions on Polygon")

    def pull_spec(self) -> None:
        attachments = {file["name"] for file in self._polygon_files().get("auxFiles") or []}
        if SPEC_FILENAME not in attachments:
            self._missing_on_polygon(SPEC_FILENAME, "no attachment on Polygon", key=SPEC_KEY)
            return
        self._sync_file(
            SPEC_FILENAME,
            lambda: self._call_raw("problem.viewFile", {"type": "aux", "name": SPEC_FILENAME}),
            key=SPEC_KEY,
            stamped=True,
        )

    def _warn_if_unparseable_solution_name(self, polygon_name: str, tag: object) -> None:
        try:
            _parse_solution_filename(Path(polygon_name))
        except PolygonStepError:
            self.warn(
                f"solutions/{polygon_name}: the name does not follow "
                "<verdict>_<language>_<description>_<author>.<ext>, so upload_solutions "
                f"will refuse to run until it is renamed (tag on Polygon: {tag})"
            )

    def _sync_file(
        self,
        name: str,
        fetch: Callable[[], bytes],
        *,
        key: str | None = None,
        also: dict[str, str] | None = None,
        detail: str = "",
        normalize: Callable[[bytes], bytes] = lambda content: content,
        stamped: bool = False,
    ) -> bool:
        """Сравнивает локальный файл `name` с версией на Polygon (`fetch`) и
        при необходимости пишет её на диск (см. докстринг модуля). Ключ базы —
        `key` (по умолчанию `name`); дополнительные записи `also` пишутся,
        только если файл теперь совпадает с Polygon. `normalize` применяется
        к содержимому перед сравнением и хешем.

        `stamped` — у файла есть отметка в листинге Polygon: она пишется в
        базу вместе с хешем, и если версия на Polygon уже известна по базе,
        файл не скачивается, пока его не нужно записать на диск.

        Возвращает, был ли файл записан. Ошибка API — запись "error" по этому
        файлу, остальные файлы той же части продолжают обрабатываться."""
        key = key or name
        also = also or {}
        stamp = self._remote_stamp(key) if stamped else None
        path = self.problem_dir / name

        known = self._known_remote_digest(key, stamp) if stamped else None
        if known is not None and path.exists():
            local = digest(normalize(path.read_bytes()))
            if local == known:
                self.add(name, ITEM_SAME, _join(detail, _NOT_DOWNLOADED_DETAIL))
                self._record(key, known, stamp, also)
                return False
            if not self.overwrite:
                self.add(name, ITEM_KEPT, _join(detail, _KEPT_DETAIL))
                self._record(key, known, stamp, {k: None for k in also})
                return False

        try:
            remote = fetch()
        except PolygonApiError as exc:
            self.add(name, ITEM_ERROR, str(exc))
            self._record(key, None, None, {k: None for k in also})
            return False

        written = False
        if not path.exists():
            self._write(name, remote)
            self.add(name, ITEM_DOWNLOADED, detail)
            written = True
        elif _same(normalize(path.read_bytes()), normalize(remote)):
            self.add(name, ITEM_SAME, detail)
        elif self.overwrite:
            self._backup(name)
            self._write(name, remote)
            self.add(name, ITEM_OVERWRITTEN, detail)
            written = True
        else:
            self.add(name, ITEM_KEPT, _join(detail, _KEPT_DETAIL))
            self._record(key, digest(normalize(remote)), stamp, {k: None for k in also})
            return False

        self._record(key, digest(normalize(path.read_bytes())), stamp, also)
        return written

    def _record(
        self, key: str, value: str | None, stamp: str | None, also: dict[str, str | None]
    ) -> None:
        """Пишет в базу хеш `key` вместе с его отметкой и записи `also`."""
        self.synced[key] = value
        self.stamps[key] = stamp
        self.synced.update(also)

    def _known_remote_digest(self, key: str, stamp: str | None) -> str | None:
        """Хеш версии `key` на Polygon, если он известен по базе без
        скачивания (см. докстринг модуля), иначе `None`."""
        base = self.base_synced.get(key)
        if base is None:
            return None
        if self.revision_unchanged or (stamp is not None and self.base_stamps.get(key) == stamp):
            return base
        return None

    def _remote_stamp(self, key: str) -> str | None:
        if self._remote_stamps is None:
            self._remote_stamps = remote_stamps(
                self._listing(self._polygon_files, {}),
                self._listing(self._polygon_solutions, []),
                self._listing(lambda: self._assigned_name("problem.validator"), None),
                self._listing(lambda: self._assigned_name("problem.checker"), None),
            )
        return self._remote_stamps.get(key)

    def _listing(self, fetch: Callable[[], object], fallback: object) -> object:
        """Листинг для отметок; упал — `fallback`: файлы без отметок просто
        скачиваются, а сама ошибка всплывёт в той части, которой листинг
        нужен (её ключи тогда не трогаются)."""
        try:
            return fetch()
        except PolygonApiError as exc:
            logger.warning("[pull] no modification times from this listing: %s", exc)
            return fallback

    def _missing_on_polygon(
        self,
        name: str,
        detail: str = "",
        *,
        key: str | None = None,
        local_only_detail: str = _LOCAL_ONLY_DETAIL,
    ) -> None:
        """Части `name` на Polygon нет: если она есть локально — "local only"
        (push её загрузит), иначе "absent". В базе её быть не должно."""
        self.synced[key or name] = None
        if (self.problem_dir / name).exists():
            self.add(name, ITEM_LOCAL_ONLY, _join(detail, local_only_detail))
        else:
            self.add(name, ITEM_ABSENT, detail)

    def _local_only(self, subdir: str, remote_names: set[str], *, pattern: str) -> list[str]:
        """Файлы `subdir/` (по `pattern`), которых нет на Polygon, — "local only".
        Помеченные шагом как удалённые (`is_removed_mark`) — не часть задачи."""
        directory = self.problem_dir / subdir
        names = sorted(
            path.name
            for path in (directory.glob(pattern) if directory.is_dir() else [])
            if path.is_file() and not is_removed_mark(path) and path.name not in remote_names
        )
        for filename in names:
            self._missing_on_polygon(f"{subdir}/{filename}")
        return names

    def _backup(self, name: str) -> None:
        target = self.backup_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((self.problem_dir / name).read_bytes())
        self.backed_up = True

    def _write(self, name: str, content: bytes) -> None:
        path = self.problem_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _polygon_files(self) -> dict:
        if self._files is None:
            self._files = self._call("problem.files") or {}
        return self._files

    def _polygon_solutions(self) -> list:
        if self._solutions is None:
            self._solutions = self._call("problem.solutions") or []
        return self._solutions

    def _assigned_name(self, method_name: str) -> str | None:
        """Имя назначенного валидатора/чекера (`problem.validator`/`problem.checker`)."""
        if method_name not in self._assigned:
            self._assigned[method_name] = self._call(method_name)
        return self._assigned[method_name]

    def _view_source(self, polygon_name: str) -> bytes:
        return self._call_raw("problem.viewFile", {"type": "source", "name": polygon_name})

    def _call(self, method_name: str, params: dict[str, str] | None = None):
        return self.client.call(method_name, {"problemId": str(self.polygon_id), **(params or {})})

    def _call_raw(self, method_name: str, params: dict[str, str]) -> bytes:
        return self.client.call_raw(method_name, {"problemId": str(self.polygon_id), **params})


def _same(local: bytes, remote: bytes) -> bool:
    return local == remote or local.replace(b"\r\n", b"\n") == remote.replace(b"\r\n", b"\n")


def _join(detail: str, suffix: str) -> str:
    return f"{detail}; {suffix}" if detail else suffix


_LIMIT_LINE = re.compile(
    rf"^(?P<head>[ \t]*(?:{_TIME_KEY}|{_MEMORY_KEY})[ \t]*:[ \t]*)"
    r"(?P<value>[^#\n]*?)(?P<tail>[ \t]*(?:#[^\n]*)?)$",
    re.MULTILINE,
)


def _replace_limits(
    text: str, values: dict[str, str], expected: tuple[int, int]
) -> str | None:
    """`text` (результат `constraints_pick`) с заменёнными значениями
    `time_limit_seconds`/`memory_limit_mb` — построчно, чтобы сохранить
    остальное содержимое и комментарии. Заменяются все вхождения ключей
    (`read_limits` ищет пару и в `limits`, и в корне). None, если после
    замены `read_limits` не прочитал бы ровно `expected` (ключи в
    flow-стиле, многострочные значения и т.п.) — тогда файл не трогаем.
    """

    def substitute(match: re.Match) -> str:
        key = match["head"].split(":", 1)[0].strip()
        return f"{match['head']}{values[key]}{match['tail']}"

    replaced = _LIMIT_LINE.sub(substitute, text)
    try:
        data = yaml.safe_load(replaced)
        if not isinstance(data, dict):
            return None
        time_limit_seconds, memory_limit_mb = _extract_limits(data)
        actual = (round(time_limit_seconds * 1000), round(memory_limit_mb))
    except (yaml.YAMLError, PolygonStepError, TypeError):
        return None
    return replaced if actual == expected else None


def _describe_limits(limits: tuple[int, int] | None) -> str:
    if limits is None:
        return "unreadable"
    return f"{limits[0]}ms/{limits[1]}MB"


def _is_plain_filename(name: str) -> bool:
    """Имя файла приходит с Polygon — не даём ему увести запись за пределы
    каталога части (`../x`, абсолютный путь)."""
    return bool(name) and name not in (".", "..") and Path(name).name == name and "\\" not in name
