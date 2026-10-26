#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int minN = opt<int>("minN");
    int maxN = opt<int>("maxN");

    auto gen_test = [&](int minN, int maxN) {
        ensuref(
            1 <= minN && minN <= maxN && maxN <= 1000,
            "Expected 1 <= minN <= maxN <= 1000, but found minN=%d, maxN=%d",
            minN, maxN
        );

        int n = rnd.next(minN, maxN);
        int x = rnd.next(1, n);

        println(n);
        println(x);
    };

    gen_test(minN, maxN);

    return 0;
}
