#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // test file: N and the favourite flags f_1..f_N
    // (already checked by the validator)
    int n = inf.readInt();

    vi f(n + 1, 0);
    int total = 0;
    for (int i = 1; i <= n; ++i) {
        f[i] = inf.readInt();
        total += f[i];
    }

    // tell the solution the number of allowed queries
    std::cout << n << std::endl;

    std::vector<bool> asked(n + 1, false);
    int queries = 0;

    while (true) {
        if (ouf.seekEof()) {
            quitf(
                _wa,
                "solution terminated after %d quer%s without the final answer \"! F\"",
                queries, (queries == 1 ? "y" : "ies")
            );
        }

        std::string type = ouf.readToken("[?!]", "query type");

        if (type == "!") {
            // final answer: the number of favourite numbers, not counted as a query
            int answer = ouf.readInt(0, n, "F");

            if (answer != total) {
                quitf(
                    _wa,
                    "wrong final answer: solution reported F = %d, expected F = %d",
                    answer, total
                );
            }

            tout << answer << ENDL;
            tout.flush();

            quitf(
                _ok,
                "correct final answer F = %d after %d quer%s",
                answer, queries, (queries == 1 ? "y" : "ies")
            );
        }

        // type == "?": a query about a single number, counted against the limit
        int x = ouf.readInt(1, n, "x");

        if (asked[x]) {
            quitf(
                _wa,
                "number %d has already been asked about earlier (query %d: ? %d)",
                x, queries + 1, x
            );
        }

        ++queries;
        if (queries > n) {
            quitf(
                _wa,
                "queries limit exceeded: more than %d queries (query %d: ? %d)",
                n, queries, x
            );
        }

        asked[x] = true;

        std::cout << (f[x] ? "YES" : "NO") << std::endl;
    }
}
