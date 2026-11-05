Extra solutions and extra machinery. Every wrong approach is a
solution to write and a test to kill it, every alternative approach is
another correct solution, a custom checker or interactor is code to write and
check.

What to look for:
- wrong approaches that fail for the same reason, on the same tests — propose
  merging;
- alternative approaches that don't differ from the main one in what they
  check;
- verdicts or languages that the other solutions already cover;
- a custom checker where a standard one compares the same answer;
- an `algorithm_hint` or behavior notes that retell the statement instead of
  the idea.

Don't propose to merge or remove tl solutions of different approaches, even
with the same asymptotics, or borderline ones that might fit the TL: they show
whether the TL separates them, and that's what they are for. Don't remove a
wrong approach because the examples already catch it: it checks that they do.
