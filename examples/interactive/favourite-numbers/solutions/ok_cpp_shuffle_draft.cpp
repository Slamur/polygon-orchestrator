#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    std::mt19937 rng(123456789u);

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
        // the order of queries is arbitrary by the statement: ask about the
        // numbers in a random order (a permutation of 1..N)
        std::vector<int> order(n);
        std::iota(order.begin(), order.end(), 1);
        std::shuffle(order.begin(), order.end(), rng);

        int favourite_count = 0;
        for (int x : order) {
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
