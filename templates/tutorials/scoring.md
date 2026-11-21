## The scoring of a problem with subtasks

- A problem with subtasks (test groups) has a separate statement block — Scoring
  in Polygon: a sentence with the total of the points, a paragraph on how the subtasks are
  scored and a table — one row per subtask: its name, its points, its extra constraints,
  its necessary subtasks and what a contestant sees about its tests.

- The orchestrator assembles the block itself from the groups and the tests: the names,
  the points (of an `each_test` subtask — with the points of one test, `30 (2)`), the
  necessary subtasks, the last column and the paragraphs are fixed. You write only the
  constraints cell of each subtask — the file `scoring.yaml`.

## scoring.yaml

```yaml
constraints:
  "1": "$N \\le 10$"
  "2": "$N \\le 10^3$, all $a_i$ are equal"
  "3": null
```

- `constraints` maps the name of every given subtask (a string, in quotes) to the text of
  its cell — LaTeX in the statement's language — or to `null` when the subtask has no
  extra constraints (the orchestrator writes "No additional constraints" /
  "Без дополнительных ограничений" then). Exactly the given subtasks, none else.
- Backslashes in a double-quoted YAML string are doubled: `"$N \\le 10$"` is the cell
  `$N \le 10$`.

## The constraints of a subtask

- `variables` — the subtask's own bounds of the named variables: `max` — `$N \le 10^5$`,
  `min` — `$K \ge 2$`, both — `$1 \le K \le 10$` (or `$K = 1$` when they are equal); the
  variable's name — as in the input format; a number — by the statement's conventions:
  powers of ten as `10^{k}`, `2 \cdot 10^5`; an expression — as a formula,
  `$K \le N - 1$`;
- `special_property` — the property in the statement's language, in formulas where it
  helps;
- `subset_of` — the subtask's tests also satisfy all constraints of that subtask: write
  those constraints out, don't just give the subtask's name;
- several of these — in one cell, separated by commas;
- none of these — `null`.

The cell is one line of text: no `&`, no `\\`, no lists or line breaks — it sits inside a
table cell.
