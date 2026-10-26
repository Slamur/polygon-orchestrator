#include "problem_lib.h"

// Simulates the author's intended binary search (mid = (lo + hi) / 2,
// '=' used immediately, '<' -> hi = mid - 1, '>' -> lo = mid + 1) for
// every candidate X in [1, n], and picks an X on which that search needs
// the largest number of queries. Used both as the author's gen_worst_x
// idea and as the adversarial test against solutions that only ask the
// final query after narrowing the range to a single candidate (which
// needs one extra query) and against off-by-one narrowing bugs.
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");

    auto gen_test = [&](int n) {
        ensuref(
            1 <= n && n <= 1000,
            "Expected n in [1, 1000], but found %d",
            n
        );

        int bestQ = -1;
        vi bestXs;

        for (int x = 1; x <= n; ++x) {
            int lo = 1, hi = n, q = 0;
            while (lo <= hi) {
                int mid = (lo + hi) / 2;
                ++q;
                if (mid == x) break;
                else if (mid < x) lo = mid + 1;
                else hi = mid - 1;
            }

            if (q > bestQ) {
                bestQ = q;
                bestXs = {x};
            } else if (q == bestQ) {
                bestXs.push_back(x);
            }
        }

        int x = rnd.any(bestXs);

        println(n);
        println(x);
    };

    gen_test(n);

    return 0;
}
