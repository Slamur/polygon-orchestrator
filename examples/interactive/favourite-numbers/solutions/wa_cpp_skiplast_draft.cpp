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
        // BUG: the last number N isn't asked about (x < n instead of x <= n),
        // the answer is counted over N - 1 answers only
        for (int x = 1; x < n; x++) {
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
