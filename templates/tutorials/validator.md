## Validator

- A **validator** is a program checking the correctness of the input data format.

- All guarantees stated in the statement must necessarily be checked in the validator.

  - Necessarily the limits on the minimal/maximal values (set by constants in the validator).
  
  - All additional guarantees in the formula limits.
  
  - All phrases with the word "it's guaranteed" and its analogues.
  
  - All guarantees following from definitions (e.g. if the problem gives a tree, it's connected by definition).

- The validator also checks whitespace.

  - No spaces at the end of a line.
  
  - A line break after each line.
  
  - No extra data at the end of the input.

- (Strongly preferred) Validators are written in C++ using [testlib.h](https://codeforces.com/testlib).

## [Testlib](https://codeforces.com/blog/entry/18426) for validating tests

- The input stream is represented by the InStream object inf.

- inf.readSpace / readEoln / readEof: checking for a space / a line break / the end of file.

- ensuref: checking conditions with formatted output in case of an error.

- inf.readInt / readLong: reading numbers within given bounds.

- inf.readLine / readWord: reading strings checked against a pattern.

- inf.readInts / readLongs / readWords: reading an array of data on one line separated by spaces.

- For all input operations a "variable name" must be given.

- setTestCase: setting the test number for multitests.

## Source file characters

- The validator source contains only ASCII characters: comments, string literals and
  messages (`ensuref`, `quitf` and the like) are written in English. Some
  compilers, Polygon's among them, fail on non-ASCII characters in a source
  file; the orchestrator reports every line with them as an error.
