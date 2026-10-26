#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");

    auto gen_test = [&]() {
        ensuref(n >= 2, "n must be at least 2, found %d", n);

        // random order of the n vertices; order[0] becomes the root
        vi order = rnd.perm(n, 1);

        vi p(n + 1, 0);
        p[order[0]] = 0;
        for (int i = 1; i < n; ++i) {
            // parent is a uniformly random vertex already placed on the prefix
            int j = rnd.next(0, i - 1);
            p[order[i]] = order[j];
        }

        println(n);
        println(vi(p.begin() + 1, p.end()));
    };

    gen_test();

    return 0;
}
