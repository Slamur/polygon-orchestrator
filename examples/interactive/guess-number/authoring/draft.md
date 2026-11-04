# Guess the number

## Statement

An interactive problem

An integer in the range [1; N] is hidden - it has to be guessed.

The program can make a query "? X", where X is an integer from the range [1; N].

A query can get one of three answers:

- "<": the hidden number is less than X
- ">": the hidden number is greater than X
- "=": the hidden number is equal to X

The program can make at most 10 queries. 

If the program gets the answer "=", it must terminate (the test is considered passed).

If the program makes more than 10 queries - it automatically gets the "wrong answer" verdict

Limits: N in [1; 1e3]

## Generator ideas

- N - a power of two + delta, delta in [-1, 0, 1]
- The hidden number is 1 or N