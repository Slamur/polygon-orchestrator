# Interactive statistics

## Statement

An interactive problem.

The jury has a capricious subsystem that likes some numbers more than others.

Your goal is to find out how many favourite numbers there are in the range 1..N.

Your program prints one number and gets "YES" (favourite) or "NO" (not favourite) in response.

In the end you have to print the number of favourite numbers found.

You may ask about each number exactly once, but you may ask in any order.

## Input

An integer N (1 <= N <= 100) - the number of queries to the system.

## Examples

N = 6 (4 favourite, 2 not)

## Interactor ideas

For each test the interactor gets N and which numbers to consider favourite.

Give WA if a number outside the range was printed or a number that has already been asked about.

And also if the final answer differs from the expected one.

## Tests

Not many tests are needed, about 10 different ones and that's it (including N = 1, N = 10, N = 100)
