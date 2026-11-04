Extra solutions and extra machinery. Every wrong approach is a
solution to write and a test to kill it, every alternative approach is
another correct solution, a custom checker or interactor is code to write and
check.

What to look for:
- wrong approaches that fail for the same reason, on the same tests — propose
  merging;
- a wrong approach that no test of a normal plan would miss anyway (it fails
  on the examples);
- alternative approaches that don't differ from the main one in what they
  check;
- verdicts or languages that the other solutions already cover;
- a custom checker where a standard one compares the same answer;
- an `algorithm_hint` or behavior notes that retell the statement instead of
  the idea.
