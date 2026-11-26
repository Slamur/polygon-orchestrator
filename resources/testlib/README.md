# testlib

[testlib](https://github.com/MikeMirzayanov/testlib): `testlib.h`, which the problems'
validators, checkers, interactors and generators include, and the standard checkers
(`checkers/*.cpp`) that Polygon offers as `std::<name>.cpp` (`ncmp`, `wcmp`, `rcmp6`,
`yesno`, …). With the standard checkers `orchestrator <problem> local run` checks the
solutions' output when the spec has `checker.custom_needed: false` and `checker.standard`
is set (`orchestrator/checks/solutions_run.py`).

- `testlib.h` and `checkers/`: commit `4f3df1d02ff0d5c8c29195b3c7dbec6db45118df`
  (2026-11-25), `testlib.h` version `0.9.52` (the `VERSION` define) — the same file
  Polygon gave the sample problems in 2026-11.
- License: MIT, the text is next to it, in `LICENSE`.
- The files are copied without changes. A build finds `#include "testlib.h"` here, after
  the problem's own `resources/` (`orchestrator/resources.py`).

The version on Polygon may differ: Polygon updates `testlib.h` of a problem with
"auto-update" on. The comparison logic of the standard checkers hasn't changed for years,
but the message text may be different.

## How to update

```bash
SHA=<testlib commit>
curl -sfL "https://raw.githubusercontent.com/MikeMirzayanov/testlib/$SHA/testlib.h" \
  -o resources/testlib/testlib.h
for f in resources/testlib/checkers/*.cpp; do
  curl -sfL "https://raw.githubusercontent.com/MikeMirzayanov/testlib/$SHA/checkers/$(basename "$f")" -o "$f"
done
```

A new checker from the same directory is added with the same `curl`, under its own name.
After updating, change the versions above and run the full `pytest`.
