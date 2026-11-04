## Checker

- A **checker** is a program checking the correctness of the participant's answer.

- The checker takes via the command line

  - the name of the input file;
  
  - the name of the file with the jury's (expected) answer;
  
  - the name of the file with the participant's (received) answer.

- On an error a verdict is thrown:

  - "WA / PE" for the participant's answer;
  
  - "CRASH" for the jury's answer.

- (Strongly preferred) The checker is written by the "[readAndCheck](https://codeforces.com/blog/entry/18431)" concept:

  - The answer is read by a function parameterized by the answer's input stream (jury / participant).
  
  - The answer's data is read and validated: the values are read right within the allowed bounds.
  
    - Whitespace validation is NOT used (unless it's part of the problem).
  
  - The data is checked for correctness and compliance with the requirements (a subset of validation).
  
  - The participant may print any part of the answer wrong — it's better to check in the mode of maximal distrust.
  
  - The function returns information sufficient for comparing the answers with each other.
  
  - The function does NOT check the answer's optimality.

- If both answers are valid and correct, they're compared with each other.

- On a mismatch the corresponding error verdict is thrown.

- On a correct answer the OK verdict is thrown.

- (Strongly preferred) Checkers are written in C++ using [testlib.h](https://codeforces.com/testlib).

## [Testlib](https://codeforces.com/blog/entry/18431) for checkers

- Input streams: InStream inf, ans and ouf for the input, the jury's answer and the participant's answer.

- The input methods are the same as in the validator: readInt / readLong (within given bounds), readLine / readWord (checked against a pattern), readInts / readLongs / readWords (an array on one line); a "variable name" is given for all of them.

- Don't call the whitespace check.

- quitf: an input stream method ending the execution with a given verdict and a formatted message.

- quitif: the same as quitf, only it ends when the condition in the first argument is true.

- ans.quitf/quitif: turn the _wa / _pe verdicts into the _fail verdict "under the hood".

## Source file characters

- The checker source contains only ASCII characters: comments, string literals and
  messages (`ensuref`, `quitf` and the like) are written in English. Some
  compilers, Polygon's among them, fail on non-ASCII characters in a source
  file; the orchestrator reports every line with them as an error.
