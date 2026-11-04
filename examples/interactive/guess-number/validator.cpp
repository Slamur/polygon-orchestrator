#include "problem_lib.h"

// Bounds from the validator.constants section of constraints.yaml.
const int minN = 1, maxN = 1000;
const int minX = 1;   
const int minQ = 0, maxQ = 10;     // a statement constant: the query limit, absent from the input file

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    // The first line: N.
    int n = inf.readInt(minN, maxN, "N");
    inf.readEoln();

    // The second line: X, 1 <= X <= N.
    inf.readInt(minX, n, "X");
    inf.readEoln();

    inf.readEof();

    return 0;
}
