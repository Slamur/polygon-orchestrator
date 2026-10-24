#include <bits/stdc++.h>

using ll = int64_t;

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
        int block = std::max(1, static_cast<int>(std::sqrt(static_cast<double>(n))));
        int blocks = (n + block - 1) / block;

        std::vector<ll> elem(n, 0);
        std::vector<ll> lazy(blocks, 0);

        for (const auto& query : queries) {
            int l = query.l - 1;
            int r = query.r - 1;
            int bl = l / block;
            int br = r / block;

            if (bl == br) {
                for (int i = l; i <= r; ++i) {
                    elem[i] += query.d;
                }
            } else {
                for (int i = l; i < (bl + 1) * block; ++i) {
                    elem[i] += query.d;
                }
                for (int b = bl + 1; b < br; ++b) {
                    lazy[b] += query.d;
                }
                for (int i = br * block; i <= r; ++i) {
                    elem[i] += query.d;
                }
            }
        }

        std::vector<ll> ans(n);
        for (int i = 0; i < n; ++i) {
            ans[i] = elem[i] + lazy[i / block];
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
