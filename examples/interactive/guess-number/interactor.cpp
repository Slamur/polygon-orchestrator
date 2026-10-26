#include "problem_lib.h"

const int MAX_QUERIES = 10;

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // test file: N and the hidden number X (already checked by the validator)
    int n = inf.readInt();
    int x = inf.readInt();

    // tell the solution the upper bound
    std::cout << n << std::endl;

    int queries = 0;
    while (true) {
        if (ouf.seekEof()) {
            quitf(
                _wa,
                "solution terminated after %d queries without receiving answer \"=\"",
                queries
            );
        }

        ouf.readToken("[?]", "query type");
        int xq = ouf.readInt(1, n, "X");

        ++queries;
        if (queries > MAX_QUERIES) {
            quitf(
                _wa,
                "queries limit exceeded: more than %d queries (query %d: ? %d)",
                MAX_QUERIES, queries, xq
            );
        }

        if (x < xq) {
            std::cout << "<" << std::endl;
        } else if (x > xq) {
            std::cout << ">" << std::endl;
        } else {
            std::cout << "=" << std::endl;

            tout << x << ENDL;
            tout.flush();

            quitf(
                _ok,
                "guessed number %d in %d quer%s",
                x, queries, (queries == 1 ? "y" : "ies")
            );
        }
    }
}
