## The scoring of a problem with subtasks

- A problem with subtasks (test groups) has a separate statement block — Scoring
  in Polygon. It tells a contestant how many points each subtask is worth, which
  extra constraints its tests satisfy, which subtasks must be passed for its points
  and what the contestant sees about its tests.

- The points of a subtask are awarded only if all tests of this subtask and of all
  its necessary subtasks are passed (`points_policy: complete_group`, the default).
  A subtask with `points_policy: each_test` is the exception: its points are split
  equally among its tests and awarded for each passed test, if all tests of its
  necessary subtasks are passed. The statement examples (subtask 0) are worth no
  points and aren't in the table.

## The block's structure

- A sentence with the total of the points (the sum of all subtasks), a paragraph about
  the "all tests of the subtask and of the necessary subtasks" rule (with `each_test`
  subtasks — the paragraph for them, see below), then a table — one row per subtask in
  the order of the subtasks, five columns:

  1. the subtask's name (its number);
  2. its points — exactly as the author set them;
  3. its extra constraints;
  4. its necessary subtasks — the names from its `dependencies`, comma-separated,
     the cell is empty if there are none;
  5. what a contestant sees about the subtask's tests (its `feedback_policy`); for an
     `each_test` subtask instead only "points per test" ("баллы за каждый тест"):
     "first error; points per test" reads as if testing stopped at the first error.

- The cells of a row are separated by `&`, a row ends with `\\` and is followed by `\hline`.
  Numbers in the cells are plain: the name and the points — digits only, without
  formatting.

- The constraints of a subtask:

  - `variables` — the subtask's own bounds of the named variables: `max` —
    `$N \le 10^5$`, `min` — `$K \ge 2$`, both — `$1 \le K \le 10$` (or `$K = 1$`
    when they are equal); the variable's name — as in the input format; a number — by
    the statement's conventions: powers of ten as `10^{k}`, `2 \cdot 10^5`; an
    expression — as a formula, `$K \le N - 1$`;
  - `special_property` — the property in the statement's language, in formulas where
    it helps;
  - `subset_of` — the subtask's tests also satisfy all constraints of that subtask:
    write those constraints out, don't just give the subtask's name;
  - none of these — "No additional constraints" ("Без дополнительных ограничений"
    in a Russian statement).

- The last column, by `feedback_policy`:

  | policy | English | Russian |
  |---|---|---|
  | `icpc` | first error | первая ошибка |
  | `points` | points only | только баллы |
  | `complete` | all tests | все тесты |
  | `none` | none | нет |

  In another language — a translation of the English one.

- With `each_test` subtasks the paragraph after the total says how they are scored; the
  number of points per test isn't written (the number of tests isn't known yet):

  - some subtasks `each_test`: after the "all tests" sentence — "The exception is the
    subtasks marked "points per test": their points are split equally among their tests
    and awarded for each passed test, if all tests of the necessary subtasks are passed."
    ("Исключение — подзадачи с пометкой «баллы за каждый тест»: их баллы делятся поровну
    между тестами подзадачи и начисляются за каждый пройденный тест, если все тесты
    необходимых подзадач успешно пройдены.");
  - every subtask `each_test`: instead of the "all tests" sentence — "The points of each
    subtask are split equally among its tests and awarded for each passed test, if all
    tests of the necessary subtasks are passed." ("Баллы за каждую подзадачу делятся
    поровну между её тестами и начисляются за каждый пройденный тест, если все тесты
    необходимых подзадач успешно пройдены.").

  A row of an `each_test` subtask: `2 & 15 & \centering $N \le 10^3$ & 1 & баллы за каждый
  тест \\`.

## The sample (a Russian statement, an English one — the same with English words)

```latex
Задача оценивается в 100 баллов (задача состоит из подзадач).

Баллы за каждую подзадачу начисляются только в случае, если все тесты для этой подзадачи
и необходимых подзадач успешно пройдены.

\begin{center}
\renewcommand{\arraystretch}{1.3}
\begin{tabular}{|c|c|p{5cm}|c|c|}
\hline
\textbf{Подзадача} &
\textbf{Баллы} &
\parbox{5cm}{\centering \textbf{Ограничения}} &
\parbox{3cm}{\textbf{\centering\\Необходимые\\подзадачи\\\vspace{2mm}}} &
\parbox{3cm}{\textbf{\centering\\Информация\\о проверке\\\vspace{2mm}}}
\\ \hline
1 & 10 & \centering $N \le 10$ &  & первая ошибка \\
\hline
2 & 15 & \centering $N \le 10^3$ & 1 & первая ошибка \\
\hline
3 & 75 & \centering Без дополнительных ограничений & 1, 2 & первая ошибка \\
\hline
\end{tabular}
\end{center}
```

English headers: `Subtask`, `Points`, `Constraints`, `Necessary\\subtasks`,
`Verification\\information`; the first sentences: "The problem is scored out of 100
points (the problem consists of subtasks)." and "Points for a subtask are awarded only
if all tests of this subtask and of the necessary subtasks are passed."
