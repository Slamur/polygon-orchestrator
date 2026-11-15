// SUGGESTION: queries are printed without the "? " prefix - the most likely
// format error, which isn't in known_wrong_approaches; any test catches it on
// the very first query.
#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    auto ask = [&](int x) {
        // BUG: the query is printed without the mandatory "? " prefix -
        // a violation of the interaction format
        std::cout << x << std::endl;
        std::string response;
        std::cin >> response;
        return response;
    };

    auto answer = [&](int favourite_count) {
        std::cout << "! " << favourite_count << std::endl;
    };

    auto count_favourites = [&]() {
        int favourite_count = 0;
        for (int x = 1; x <= n; x++) {
            if (ask(x) == "YES") {
                favourite_count++;
            }
        }
        return favourite_count;
    };

    answer(count_favourites());
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
