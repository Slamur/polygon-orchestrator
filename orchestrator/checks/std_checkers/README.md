# Standard testlib checkers

Sources of the standard checkers that Polygon offers as `std::<name>.cpp`
(`ncmp`, `wcmp`, `rcmp6`, `yesno`, …). With them `orchestrator <problem> local run`
checks the solutions' output on the examples when the spec has `checker.custom_needed: false`
and `checker.standard` is set (`orchestrator/checks/solutions_run.py`).

- Source: <https://github.com/MikeMirzayanov/testlib>, the `checkers/` directory.
- Commit: `3bf5ba5aca6007bf8dcde6d00949ec3116ee3cc7` (2026-10-26).
- License: MIT, the text is next to it, in `LICENSE`.
- The files are copied without changes. They are built with `templates/testlib.h`
  (`#include "testlib.h"` is found via `-I templates`).

The version on Polygon may differ slightly from this commit. The comparison logic
of the standard checkers hasn't changed for years, but the message text may be different.

## How to update

```bash
SHA=<testlib commit>
for f in orchestrator/checks/std_checkers/*.cpp; do
  curl -sfL "https://raw.githubusercontent.com/MikeMirzayanov/testlib/$SHA/checkers/$(basename "$f")" -o "$f"
done
```

A new checker from the same directory is added with the same `curl`, under its own name.
After updating, change the commit above and run `pytest`.
