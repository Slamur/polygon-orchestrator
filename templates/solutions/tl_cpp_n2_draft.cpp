#include <bits/stdc++.h>

using ll = int64_t;

bool yes_no(bool yes, const std::string& yes_str = "YES", const std::string& no_str = "NO") {
    std::cout << (yes ? yes_str : no_str) << "\n";
    return yes;
}

void solve() {
    int n;
    ll k;

    std::cin >> n >> k;

    std::vector<int> a(n);
    for (auto& v : a) {
        std::cin >> v;
    }

    auto get_ans = [&]() {
        for (int i = 0; i < n; ++i) {
            ll pref = 0;
            for (int j = 0; j <= i; ++j) {
                pref += a[j];
            }

            if (pref > k) return i;
        }

        return -1;
    };

    auto ans = get_ans();
    if (yes_no(ans >= 0)) {
        std::cout << ans + 1 << "\n";
    }
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}