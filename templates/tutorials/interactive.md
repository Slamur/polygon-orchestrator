## An interactive problem

- In an interactive problem the solution doesn't read the test itself: it communicates with
  the **interactor** — a jury program that reads the test and answers the
  solution's queries by the **interaction protocol**.

- The test (the interactor's input) is the jury's data, usually hidden from the solution
  (a hidden number, a hidden array). It's checked by the validator and produced by
  the generators — the same as the input of a normal problem. The solution sees only what
  the interactor told it.

- The protocol is set by the author: what the solution gets at the start, the query format,
  the format of the interactor's answers, the limit on the number of queries, how the solution reports
  the final answer and what it does after it (usually — exits).

- The solution must flush the output buffer after each query and after
  the final answer, otherwise the interactor won't get the query, and the solution gets
  Idleness limit exceeded.

## The statement of an interactive problem

- The protocol is a separate statement block (Interaction in Polygon), it has:

  - what the solution reads at the very start (the format and the limits, as in the input
    format of a normal problem);

  - the format of each query of the solution and of each answer of the interactor, a line per
    portion of data, with limits on the values;

  - the limit on the number of queries and what happens when it's exceeded or on an
    invalid query (Wrong answer);

  - the format of the final answer and what the solution does after it;

  - a mandatory reminder to flush the output buffer after each printed
    line — with the recommended way for popular languages:

    - C++: `std::endl` when printing via `iostream` (`cout << ... << endl`),
      `fflush(stdout)` after printing via `stdio` (`printf`);

    - Java: `System.out.println(...)`, and when printing via another stream
      (`PrintWriter`, `BufferedWriter`) — its `flush()` after each line;

    - Python: `print(..., flush=True)` and reading the answers via `input()`;

  - whether the interactor is adaptive (whether it may change the hidden data
    depending on the queries, staying consistent with the answers already
    given) — only if the author's protocol says so.

- The input format and the output format in an interactive problem are short: what the solution
  gets at the start and a reference to the interaction protocol; don't describe in them
  the test the interactor reads (the hidden data isn't told to the solution).

- An example in the statement is a dialogue of the solution with the interactor, not a test:

  - on the left (input for statement) — everything the solution read: the initial
    data and the interactor's answers;

  - on the right (output for statement) — everything the solution printed: the queries and
    the final answer;

  - the lines go in the order of the dialogue; so that both columns read
    together, an empty line is left in place of the other side's line
    (a line of the interactor's answer stands opposite an empty line in the solution's output
    and vice versa);

  - the dialogue strictly matches the protocol and the example's test: the interactor's
    answers are those it would give on this test, the queries are correct,
    within the limit.

- The note to an example explains why the interactor answered exactly that way and
  why the final answer is correct.

## The interactor

- The interactor is written in C++ with testlib and starts with
  `registerInteraction(argc, argv)`.

- The streams:

  - `inf` — the test (the interactor's input), read with testlib methods
    (`inf.readInt()`, `inf.readInts(n)`) without validating it again — the test is already
    checked by the validator;

  - `ouf` — the solution's output: the queries and the final answer; read in the mode of
    maximal distrust, right within the allowed bounds
    (`ouf.readInt(1, n, "x")`, `ouf.readToken("[?!]", "type")`) — a violation of the
    format or the bounds gives the solution a verdict, not a crash of the interactor;

  - `cout` — the interactor's answers to the solution; after each answer —
    `cout.flush()` (or `cout << endl`);

  - `tout` — the interactor's output file, which the checker then reads as the
    participant's output; what to write into it is set by the author.

- Verdicts: `quitf(_wa, ...)` — an invalid query, the limit exceeded, a wrong
  final answer; `quitf(_pe, ...)` — a format violation (testlib gives it
  itself when reading from `ouf`); `quitf(_ok, ...)` — the solution succeeded;
  `quitf(_fail, ...)` — a jury error (a contradictory test). The verdict's
  message is meaningful: which query, which value, which limit.

- The interactor counts the queries and checks the limit before answering a
  query beyond the limit.

- The interactor doesn't trust the solution: each value read is checked
  for compliance with the protocol (the range, the query type, admissibility in the current
  state), before answering the query.

- If the solution exited before the final answer, reading from `ouf`
  gives the verdict itself (an unexpected end of file) — this isn't handled
  separately.

- A non-adaptive interactor takes the hidden data from the test entirely and doesn't change
  it. An adaptive one — only if the author set this explicitly, and only the way it's
  set: the answers stay consistent with all those already given.

## The solution of an interactive problem

- Input/output — the same as in the author's normal solution, but the output is flushed
  after each query and after the final answer (fast buffered
  output without flushing doesn't fit here: the interactor won't see the query).

- The solution reads nothing except what the interactor tells it by the
  protocol, and prints nothing beyond the protocol (debug output — only
  to stderr).

- A solution without flushing the output buffer (`noflush`) isn't a TL: the solution's process doesn't
  spend CPU time but waits for an answer from the interactor that won't come,
  and gets Idleness limit exceeded. The verdict of such a solution in the
  file name is `il` (on Polygon — Incorrect, like RE), not `tl` and not `tl_ok` —
  even if the author wrote `tl` in the solution's item; in that case write `il` and
  mark the replacement in the notes. By language:

  - Java — output via `PrintWriter`/`BufferedWriter` without `flush()`;

  - C++ — output via `cout` without `endl`/`flush()` with untied input
    (`ios_base::sync_with_stdio(false)` and `cin.tie(nullptr)`) or via
    `printf` without `fflush(stdout)`. If `cin` isn't untied from `cout`, reading
    from `cin` flushes `cout` itself, and such a solution passes — so a
    `noflush` solution in C++ necessarily unties `cin`;

  - Python — `print(...)` without `flush=True` and reading the answers via
    `sys.stdin.readline()` (in the line-reading function). `print()` doesn't flush the buffer
    itself, while `input()` flushes `sys.stdout` before reading, and a
    solution with `input()` passes — so a `noflush` solution in Python
    reads via `sys.stdin.readline()`, not via `input()`.

  If the author asks for a `noflush` solution that by these rules would flush the
  buffer itself (C++ with tied `cin`, Python with `input()`) — write it in the
  `noflush` variant described above, and mark this in the notes.

## Source file characters

- The interactor and solution sources contain only ASCII characters: comments, string literals and
  messages (`ensuref`, `quitf` and the like) are written in English. Some
  compilers, Polygon's among them, fail on non-ASCII characters in a source
  file; the orchestrator reports every line with them as an error.
