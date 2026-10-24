#include <bits/stdc++.h>

// BUG: 32-bit accumulator instead of 64-bit, values reach Q * max|D| = 3 * 10^14
using ll = int32_t;

struct Query {
    int l;
    int r;
    int d;
};

void solve() {
    int n, q;

    std::cin >> n >> q;

    std::vector<Query> queries(q);
    for (auto& query : queries) {
        std::cin >> query.l;
    }
    for (auto& query : queries) {
        std::cin >> query.r;
    }
    for (auto& query : queries) {
        std::cin >> query.d;
    }

    auto get_ans = [&]() {
        std::vector<ll> inc(n + 2, 0);
        for (const auto& query : queries) {
            inc[query.l] += query.d;
            inc[query.r + 1] -= query.d;
        }

        std::vector<ll> ans(n);
        ll cur = 0;
        for (int i = 1; i <= n; ++i) {
            cur += inc[i];
            ans[i - 1] = cur;
        }

        return ans;
    };

    auto ans = get_ans();
    for (int i = 0; i < n; ++i) {
        std::cout << ans[i] << (i + 1 < n ? ' ' : '\n');
    }
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
