#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int h = opt<int>("h");
    int k = opt<int>("k");
    int palms = opt<int>("palms");

    auto gen_test = [&]() {
        ensuref(n >= 2, "n must be at least 2, found %d", n);
        ensuref(palms >= 1, "palms must be at least 1, found %d", palms);
        ensuref(h >= 1, "h must be at least 1, found %d", h);
        ensuref(k >= 1, "k must be at least 1, found %d", k);

        int stalkVertices = palms * (h - 1);
        ensuref(
            1 + stalkVertices <= n,
            "n=%d is too small for palms=%d stalks of height h=%d",
            n, palms, h
        );

        vi p(n + 1, 0);
        int root = 1;

        // palms stalks of height h sharing the root; tip[j] is the end of stalk j
        vi tip(palms);
        int next_id = 2;
        for (int j = 0; j < palms; ++j) {
            int cur = root;
            for (int i = 0; i < h - 1; ++i) {
                p[next_id] = cur;
                cur = next_id;
                ++next_id;
            }
            tip[j] = cur;
        }

        // leaves: chains of length k, distributed round-robin among the palm tips,
        // filling all remaining vertices
        int remaining = n - next_id + 1;
        int j = 0;
        while (remaining > 0) {
            int len = std::min(k, remaining);
            int cur = tip[j % palms];
            for (int i = 0; i < len; ++i) {
                p[next_id] = cur;
                cur = next_id;
                ++next_id;
            }
            remaining -= len;
            ++j;
        }

        println(n);
        println(vi(p.begin() + 1, p.end()));
    };

    gen_test();

    return 0;
}
