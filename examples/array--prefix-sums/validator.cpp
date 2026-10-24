#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    const int minN = 1, maxN = 2e5;
    const ll minK = 0, maxK = 1e15;
    const int minA = 0, maxA = 1e9;

    int n = inf.readInt(minN, maxN, "n");
    inf.readSpace();

    ll k = inf.readLong(minK, maxK, "k");
    inf.readEoln();

    vi a = inf.readInts(n, minA, maxA, "a");
    inf.readEoln();

    inf.readEof();
}