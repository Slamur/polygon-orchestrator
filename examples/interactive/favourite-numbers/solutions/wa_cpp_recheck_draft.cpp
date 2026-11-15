#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    auto ask = [&](int x) {
        std::cout << "? " << x << std::endl;
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
            std::string response = ask(x);
            // BUG: the answer "NO" is rechecked by a repeated query about the
            // same number, and asking about a number twice is forbidden
            if (response == "NO") {
                response = ask(x);
            }
            if (response == "YES") {
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
