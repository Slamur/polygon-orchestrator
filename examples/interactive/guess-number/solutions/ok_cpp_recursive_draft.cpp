#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    auto ask = [&](int x) {
        std::cout << "? " << x << std::endl;
        std::string answer;
        std::cin >> answer;
        return answer;
    };

    std::function<void(int, int)> guess = [&](int lo, int hi) {
        if (lo > hi) {
            return;
        }
        int mid = (lo + hi) / 2;
        std::string answer = ask(mid);
        if (answer == "=") {
            return;
        }
        if (answer == "<") {
            guess(lo, mid - 1);
        } else {
            guess(mid + 1, hi);
        }
    };

    guess(1, n);
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
