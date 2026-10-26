#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int p = opt<int>("p");
    int delta = opt<int>("delta");
    int x = opt<int>("x");

    auto gen_test = [&](int p, int delta, int x) {
        ensuref(
            -1 <= delta && delta <= 1,
            "Expected delta in {-1, 0, 1}, but found %d",
            delta
        );

        ll n = (1LL << p) + delta;
        ensuref(
            1 <= n && n <= 1000,
            "Expected 2^%d + (%d) to lie in [1, 1000], but found %lld",
            p, delta, n
        );
        ensuref(
            1 <= x && x <= n,
            "Expected x in [1, %lld], but found %d",
            n, x
        );

        println(n);
        println(x);
    };

    gen_test(p, delta, x);

    return 0;
}
