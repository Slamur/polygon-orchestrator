## The reference solution

- Essentially represents the vision of what the expected contestant solution should be.

- Implemented in one of the programming languages available to the contestants.

- Used to generate the answers to the tests.

- May use the following optimizations:

  - buffered input/output;

  - using primitive types instead of reference (object) ones;

  - other optimizations that are "usual" for the language used.

- (Strongly preferred) Implemented without excessive optimizations:

  - pragmas in C++;

  - implementing collections on primitives in Java;

  - character-by-character/byte-by-byte input;

  - additional conditionals to bypass specific cases (unless it affects the overall asymptotics);

  - other local optimizations that aren't important for the main idea of the solution.

## Additional solutions

- Correct solutions using another approach (fully or partially);

  - Including solutions with other asymptotics but the expected OK verdict.

- Correct solutions in languages other than the reference solution's language.

- Wrong answer solutions:

  - a wrong approach;

  - an off-by-one ($\pm 1$) mistake;

  - missing one of the aspects of the solution;

- Solutions exceeding the time / memory limits;

  - a "forbidden" asymptotics with excessive optimizations;

    - necessarily implemented in C++ if possible.

  - a "forbidden" asymptotics with conditions on specific kinds of tests;

    - necessarily implemented in C++ if possible.

  - input/output without speedup

    - necessarily implemented in Java / Python if it makes sense for the problem.

## Solutions in Polygon

- The name template: <verdict><language><description>_<author>.<extension>:

  - verdict — ok / tl / wa / re / il or combinations (tl_ok — "TL or OK";
    il — Idleness limit exceeded, happens only in an interactive problem);

  - language — usually duplicates the extension;

  - Polygon ignores the extension when comparing file names;

  - description — essentially the solution's tags;

  - author — a short nickname;

- Exactly one solution is Main correct: the reference for generating the answers to the tests;

- We try to make an OK for all different solution ideas;

  - It's usually undesirable to optimize the constant too much;

  - It's preferable to have a solution without input/output optimization (may be "TL or OK");

- We try to make a WA for all possible typos / +-1 / mistakes in inequalities;

  - If a solution doesn't pass the samples, we keep it only if it passes practically all tests;

- We try to add a TL in the fastest language;

  - All available input/output speedups;

  - Pragmas (for C++);

  - All kinds of heuristics / pruning.

- With different wrong verdicts we set Incorrect.

## Source file characters

- Solution sources in every language contain only ASCII characters: comments, string literals and
  messages (`ensuref`, `quitf` and the like) are written in English. Some
  compilers, Polygon's among them, fail on non-ASCII characters in a source
  file; the orchestrator reports every line with them as an error.
