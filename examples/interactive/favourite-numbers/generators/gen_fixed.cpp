#include "problem_lib.h"
#include <sstream>

// Positions are passed as a single comma-separated string (e.g. -f=1,0,1)
// with no spaces and no surrounding quotes, since Polygon generator
// parameters can't contain quotes or the '>' sign.
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    std::string fRaw = opt<std::string>("f");

    auto gen_test = [&](const std::string& fRaw) {
        vi f;
        std::stringstream ss(fRaw);
        std::string token;
        while (std::getline(ss, token, ',')) {
            ensuref(
                token == "0" || token == "1",
                "Expected every token of f to be '0' or '1', but found '%s'",
                token.c_str()
            );
            f.push_back(std::stoi(token));
        }

        int n = (int) f.size();
        ensuref(
            1 <= n && n <= 100,
            "Expected |f| (N) in [1, 100], but found %d",
            n
        );

        println(n);
        println(f);
    };

    gen_test(fRaw);

    return 0;
}
