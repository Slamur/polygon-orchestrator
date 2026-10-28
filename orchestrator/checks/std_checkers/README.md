# Стандартные чекеры testlib

Исходники стандартных чекеров, которые Polygon предлагает как `std::<имя>.cpp`
(`ncmp`, `wcmp`, `rcmp6`, `yesno`, …). Ими `orchestrator <задача> local run`
проверяет вывод решений на примерах, когда в спеке `checker.custom_needed: false`
и задан `checker.standard` (`orchestrator/checks/solutions_run.py`).

- Источник: <https://github.com/MikeMirzayanov/testlib>, каталог `checkers/`.
- Коммит: `3bf5ba5aca6007bf8dcde6d00949ec3116ee3cc7` (2026-10-26).
- Лицензия: MIT, текст лежит рядом, в `LICENSE`.
- Файлы скопированы без изменений. Собираются с `templates/testlib.h`
  (`#include "testlib.h"` находится через `-I templates`).

Версия на Polygon может немного отличаться от этого коммита. Логика сравнения
у стандартных чекеров годами не меняется, но текст сообщений может быть другим.

## Как обновить

```bash
SHA=<коммит testlib>
for f in orchestrator/checks/std_checkers/*.cpp; do
  curl -sfL "https://raw.githubusercontent.com/MikeMirzayanov/testlib/$SHA/checkers/$(basename "$f")" -o "$f"
done
```

Новый чекер из того же каталога добавляется тем же `curl`, под своим именем.
После обновления поменять коммит выше и прогнать `pytest`.
