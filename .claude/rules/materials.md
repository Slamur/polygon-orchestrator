---
paths:
  - "templates/**"
  - "examples/**"
  - "orchestrator/references.py"
  - "docs/SPEC_FORMAT.md"
  - "tests/test_example_templates.py"
  - "tests/test_references.py"
---

# Приложенные материалы: templates/, examples/, референсы

## Как использовать приложенные материалы


- `templates/tutorials/*.md` (кроме `polygon.md`) — это **правила**, а не справочный
  материал "для общего развития". Промпты должны явно инструктировать
  модель следовать им как обязательным ограничениям, а не как совету.
  Правила разбиты по темам, и шаг получает только нужные ему
  (`STEP_CONTEXT_DOCUMENTS`), поэтому правка одной темы инвалидирует кэш
  только читающих её шагов. Файлы пишутся для модели: каждый
  самодостаточен, без ссылок на другие файлы правил. `polygon.md` — справка
  по интерфейсу Polygon, ни одному шагу не прикладывается.
- `templates/problem_lib.h` и файлы учебных задач
  `examples/<путь задачи>/` (`validator.cpp`, `checker.cpp`,
  `generators/*.cpp`, `test_script`, `solutions/*`)
  — это основа, которую нужно расширять, а не переизобретать. Если шаг
  генерации создаёт новый генератор — он должен `#include "problem_lib.h"` и
  следовать тем же соглашениям об именовании (`register...`, `opt<T>(...)`,
  стиль `gen_test` из `generators/*.cpp`). У каждой задачи своя копия
  `problem_lib.h` (`problems/<путь задачи>/problem_lib.h`, см.
  `PROBLEM_LIB_FILENAME` в `orchestrator/steps/base.py`): шаблон — только её
  начальное содержимое, правка шаблона в уже существующие копии не
  попадает. Compile-check ищет `problem_lib.h` сначала в каталоге задачи,
  потом в `templates/`; модели прикладывается шаблон.
- `templates/` — общее для всех задач и прикладывается к шагам
  всегда (`STEP_CONTEXT_DOCUMENTS` в `orchestrator/steps/base.py`); там же
  `constraints_format.yaml` — формат результата `constraints_pick`, который
  читают следующие шаги и `polygon push`, и `tests_plan_format.yaml` —
  формат плана тестов. `examples/` — учебные
  задачи, по каталогу `<вид входных данных>/<суть задачи>` на каждую
  (`array/prefix-sums`), в раскладке `problems/<путь задачи>/` плюс свой
  спек `authoring/spec.yaml` (`problem_id` — путь через `--`, `array--prefix-sums`;
  служебные файлы оркестратора в них — кэш, логи, `polygon_state.json`,
  `.pull_backup/`, `.llm_backup/`, `.llm_conflicts/`, `.fix_backup/`, `.tests_cache/`, `llm_reports/`, `*.removed` — в `.gitignore`;
  `limits.yaml` коммитится — актуальные TL/ML задачи, после подгонки на
  Polygon они расходятся с `constraints.yaml`, и это нормально; ожидаемый по нему
  результат `constraints_pick` — `authoring/constraints.yaml` рядом, `tests_plan`
  — `authoring/tests_plan.yaml` (его генераторы — ровно `generators/*.cpp` с
  теми же параметрами, и он покрывает все пункты спека); при правке
  образцов держать их согласованными с ними и с `constraints_format.yaml`
  — это проверяет `tests/test_example_templates.py`, там же compile-check
  всех `.cpp` образцов).
- `templates/solutions/<verdict>_<description>.<language>` — скелеты
  решений (`ok.cpp`, `tl_ok_slowio.java`): способ ввода-вывода автора,
  `solve()`, отдельное вычисление ответа и точка входа, без алгоритма;
  язык скелета — его расширение. `solutions_draft` прикладывает все
  скелеты каждого языка из `solutions.languages`, для которого референсы
  не дали ни одного `ok`-решения (`extra_context_documents` в
  `orchestrator/steps/solutions_draft.py`), — так ввод-вывод задан и без
  референсов. Новый язык или вердикт — новый файл с расширением, как язык
  назван в именах решений (`ok.kt`), код править не нужно.
- Образцы (файлы других задач) прикладываются к вызову модели только как
  **референсы спека** — поле `references` в корне спека (для всех шагов) и
  в секциях шагов (только для своего шага), см. `docs/SPEC_FORMAT.md`,
  "Референсы", и `orchestrator/references.py`. Референс — файл или каталог
  в раскладке `problems/<путь задачи>/` (учебная задача из
  `examples/`, прошлая задача автора в `problems/`); каждый шаг
  берёт из каталога только свою часть (`REFERENCE_DOCUMENTS`), решения —
  только на языках из `solutions.languages`. Без референсов шаг получает
  только `templates/`. Содержимое референсов входит в хеш промпта
  шага (см. "Кэширование по хешу спека" в
  `.claude/rules/steps.md`), сами пути `references` в
  `input_hash` не входят. Спек и `constraints.yaml` референса модель видит
  под путём без `authoring/` (`examples/<путь задачи>/constraints.yaml`,
  `_context_document_key` в `orchestrator/steps/base.py`) — под тем же
  именем, под которым шаг возвращает свой `constraints.yaml`. Промпты обязаны явно
  разделять, что из образца переносить (каркас файла, ввод-вывод, функции
  testlib, композицию генераторов через конвейер), а что нет (формат данных,
  имена переменных и логику задачи-образца), и работать без образцов — см.
  `docs/PROMPTS.md`, "Приложенные шаблоны как образцы".


## Соглашения об именах в образцах

- Генераторы: `gen_*.cpp` порождают вход, `upd_*.cpp` преобразуют готовый тест.
- Решения учебных задач: `<verdict>_<language>_<description>_draft.<ext>`;
  скелеты в `templates/solutions/` — `<verdict>_<description>.<language>`.
- Спек учебной задачи — `authoring/spec.yaml` (`problem_id` — путь через `--`),
  рядом ожидаемые `authoring/constraints.yaml` и `authoring/tests_plan.yaml`.
