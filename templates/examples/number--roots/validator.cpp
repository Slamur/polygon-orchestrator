#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    const ll minN = 1, maxN = 1e18;

    inf.readLong(minN, maxN, "n");
    inf.readEoln();

    inf.readEof();
}
