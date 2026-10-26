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
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            std::string answer = ask(mid);
            if (answer == "=") {
                return;
            }
            if (answer == "<") {
                // BUG: off-by-one — hi = mid вместо hi = mid - 1, кандидат mid
                // остаётся в отрезке, из-за чего при N = 1000 нужно 11 запросов
                hi = mid;
            } else {
                lo = mid + 1;
            }
        }
    };

    guess();
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
