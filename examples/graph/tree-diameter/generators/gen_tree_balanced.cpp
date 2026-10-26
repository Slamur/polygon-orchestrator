#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int k = opt<int>("k");

    auto gen_test = [&]() {
        ensuref(n >= 2, "n must be at least 2, found %d", n);
        ensuref(k >= 1, "k must be at least 1, found %d", k);

        vi p(n + 1, 0);
        vi depth(n + 1, 0);
        vi candidates = {1}; // vertices still eligible to be a parent (depth < k)

        // random order in which the remaining vertices are attached
        vi order = rnd.perm(n - 1, 2);

        for (int v : order) {
            int parent = rnd.any(candidates);
            p[v] = parent;
            depth[v] = depth[parent] + 1;
            if (depth[v] < k) candidates.push_back(v);
        }

        println(n);
        println(vi(p.begin() + 1, p.end()));
    };

    gen_test();

    return 0;
}
