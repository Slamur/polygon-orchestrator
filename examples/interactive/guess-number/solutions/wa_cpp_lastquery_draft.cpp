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
            // BUG: ответ "=" не используется сразу — отрезок сужается до
            // единственного кандидата, который спрашивается ещё раз;
            // при N = 1000 это требует 11 запросов
            if (answer == "<") {
                hi = mid - 1;
            } else if (answer == ">") {
                lo = mid + 1;
            } else {
                lo = mid;
                hi = mid;
            }
        }
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
