#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    const int minN = 1, maxN = 3e5;
    const int minQ = 1, maxQ = 3e5;
    const int minLR = 1;
    const int minD = -1e9, maxD = 1e9;

    int n = inf.readInt(minN, maxN, "n");
    inf.readSpace();

    int q = inf.readInt(minQ, maxQ, "q");
    inf.readEoln();

    vi l = inf.readInts(q, minLR, n, "L");
    inf.readEoln();

    vi r = inf.readInts(q, minLR, n, "R");
    inf.readEoln();

    inf.readInts(q, minD, maxD, "D");
    inf.readEoln();

    inf.readEof();

    for (int i = 0; i < q; ++i) {
        ensuref(
            l[i] <= r[i],
            "Expected L[%d] <= R[%d], but found L[%d] = %d, R[%d] = %d",
            i + 1, i + 1, i + 1, l[i], i + 1, r[i]
        );
    }
}
