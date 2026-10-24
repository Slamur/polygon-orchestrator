#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    const int minN = 1, maxN = 3e5;
    const int minQ = 1, maxQ = 3e5;
    const int minL = 1;
    const int minD = -1e9, maxD = 1e9;

    int n = inf.readInt(minN, maxN, "n");
    inf.readSpace();

    int q = inf.readInt(minQ, maxQ, "q");
    inf.readEoln();

    vi l = inf.readInts(q, minL, n, "L");
    inf.readEoln();

    for (int i = 0; i < q; ++i) {
        if (i > 0) inf.readSpace();
        inf.readInt(l[i], n, format("R[%d]", i + 1));
    }
    inf.readEoln();

    inf.readInts(q, minD, maxD, "D");
    inf.readEoln();

    inf.readEof();
}
