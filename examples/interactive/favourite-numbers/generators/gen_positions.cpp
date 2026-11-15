#include "problem_lib.h"
#include <sstream>

// SUGGESTION: specific_test_ideas[6] requires tests where favourite numbers
// are only the range edges (1 and N) or only the middle. The author's
// gen_fixed lists every f_i as a separate command-line token, which is
// impractical for N = 100. gen_positions implements the same scenario
// compactly via a list of positions to mark favourite, for any N.
//
// Positions are passed as a single comma-separated string (e.g. -idx=1,100)
// with no spaces and no surrounding quotes, since Polygon generator
// parameters can't contain quotes or the '>' sign.
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    std::string idxRaw = opt<std::string>("idx");

    auto gen_test = [&](int n, const std::string& idxRaw) {
        ensuref(
            1 <= n && n <= 100,
            "Expected n in [1, 100], but found %d",
            n
        );

        vi idx;
        std::stringstream ss(idxRaw);
        std::string token;
        while (std::getline(ss, token, ',')) {
            ensuref(
                !token.empty(),
                "Expected no empty tokens in idx='%s'",
                idxRaw.c_str()
            );
            idx.push_back(std::stoi(token));
        }

        ensuref(
            !idx.empty(),
            "Expected idx to be non-empty"
        );

        vi f(n, 0);
        for (int p : idx) {
            ensuref(
                1 <= p && p <= n,
                "Expected every idx value in [1, %d], but found %d",
                n, p
            );
            ensuref(
                f[p - 1] == 0,
                "Expected distinct idx values, but found duplicate %d",
                p
            );
            f[p - 1] = 1;
        }

        println(n);
        println(f);
    };

    gen_test(n, idxRaw);

    return 0;
}
