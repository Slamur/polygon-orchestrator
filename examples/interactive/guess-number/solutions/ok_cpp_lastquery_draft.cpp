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

    auto guess = [&]() {
        int lo = 1;
        int hi = n;
        while (lo < hi) {
            int mid = (lo + hi) / 2;
            std::string answer = ask(mid);
            if (answer == "=") {
                return;
            }
            if (answer == "<") {
                hi = mid - 1;
            } else {
                lo = mid + 1;
            }
        }
        // Цикл идёт, пока кандидатов больше одного, поэтому последний
        // спрашивается отдельно: в лимит 10 запросов это укладывается.
        ask(lo);
    };

    guess();
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
