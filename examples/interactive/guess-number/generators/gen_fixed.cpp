#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int x = opt<int>("x");

    auto gen_test = [&](int n, int x) {
        ensuref(
            1 <= n && n <= 1000,
            "Expected n in [1, 1000], but found %d",
            n
        );
        ensuref(
            1 <= x && x <= n,
            "Expected x in [1, %d], but found %d",
            n, x
        );

        println(n);
        println(x);
    };

    gen_test(n, x);

    return 0;
}
