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
        // BUG: binary search over the range [2, N - 1] - the numbers 1 and N are never
        // asked, so the extreme values aren't found
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
