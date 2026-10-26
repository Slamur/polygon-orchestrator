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
        // BUG: бинпоиск по отрезку [2, N - 1] — числа 1 и N никогда
        // не спрашиваются, поэтому крайние значения не находятся
        int lo = 2;
        int hi = n - 1;
        while (lo <= hi) {
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
    };

    guess();
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
