# Tree diameter

## Statement

Given a tree of N vertices.

Find any diameter of the tree.

Input: 

N
the array of parents (-1 if there is none)

Output: 

D S E - the diameter, the start and the end of the diameter
If there are several diameters - print any

## Generator ideas

- a random tree
    - a permutation is generated
    - each vertex's parent is random on the prefix

- a palm
    - a "trunk" of length H
        - denote the root by R and the opposite end by P
    - a "leaf" is a chain of length K
        - each leaf is attached to P
    - a basic palm example: H = N / 2, K = 1

- a jungle
    - several palms attached to one root
    - maybe it makes sense to make a common generator, just fixing the number of palms as a parameter

- a balanced tree
    - all paths from the root have length at most K