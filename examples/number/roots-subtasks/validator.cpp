#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    const ll minN = 1, maxN = 1e18;

    // The largest N of each test group; group 0 - the statement examples.
    const ll e1 = 10, e3 = 1e3, e6 = 1e6, e12 = 1e12;
    const std::vector<ll> group_max_n = {
        maxN, // 0: examples
        e1,   // 1
        e3,   // 2
        e6,   // 3
        e12,  // 4
        maxN  // 5: full
    };

    const int group = group_index();
    ensuref(
        0 <= group && group < (int)group_max_n.size(),
        "Unknown test group %d",
        group
    );

    inf.readLong(minN, group_max_n[group], "n");
    inf.readEoln();

    inf.readEof();
}
