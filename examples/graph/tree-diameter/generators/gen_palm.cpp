#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int h = opt<int>("h");
    int k = opt<int>("k");

    auto gen_test = [&]() {
        ensuref(n >= 2, "n must be at least 2, found %d", n);
        ensuref(1 <= h && h <= n, "h must be in [1, n], found %d (n=%d)", h, n);
        ensuref(k >= 1, "k must be at least 1, found %d", k);

        vi p(n + 1, 0);

        // stalk: vertices 1..h form a chain; vertex 1 is the root R, vertex h is the tip P
        for (int v = 2; v <= h; ++v) p[v] = v - 1;
        int tip = h;

        // leaves: chains of length k attached to the tip, filling vertices h+1..n
        int next_id = h + 1;
        int remaining = n - h;
        while (remaining > 0) {
            int len = std::min(k, remaining);
            int cur = tip;
            for (int i = 0; i < len; ++i) {
                p[next_id] = cur;
                cur = next_id;
                ++next_id;
            }
            remaining -= len;
        }

        println(n);
        println(vi(p.begin() + 1, p.end()));
    };

    gen_test();

    return 0;
}
