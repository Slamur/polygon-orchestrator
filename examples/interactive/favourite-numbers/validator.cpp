#include "problem_lib.h"

// Bounds from the validator.constants section of constraints.yaml.
const int minN = 1, maxN = 100;
const int minF = 0, maxF = 1;
const int minQ = 0;     // a statement constant: at most N queries, absent from the input file

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    // The first line: N.
    int n = inf.readInt(minN, maxN, "N");
    inf.readEoln();

    // The second line: exactly n flags f_1..f_N, each from {0, 1}.
    // Any subset of favourite numbers is allowed (including the empty one and
    // all numbers at once), so there are no restrictions on the sum of f_i.
    inf.readInts(n, minF, maxF, "f");
    inf.readEoln();

    inf.readEof();

    return 0;
}
