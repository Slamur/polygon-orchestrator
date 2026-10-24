#include <bits/stdc++.h>

using ll = int64_t;

struct Query {
    int l;
    int r;
    int d;
};

class Fenwick {
public:
    explicit Fenwick(int n) : n_(n), bit_(n + 1, 0) {}

    void add(int pos, ll d) {
        for (; pos <= n_; pos += pos & -pos) {
            bit_[pos] += d;
        }
    }

    ll prefix_sum(int pos) const {
        ll res = 0;
        for (; pos > 0; pos -= pos & -pos) {
            res += bit_[pos];
        }

        return res;
    }

private:
    int n_;
    std::vector<ll> bit_;
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
        Fenwick fenwick(n);
        for (const auto& query : queries) {
            fenwick.add(query.l, query.d);
            if (query.r + 1 <= n) {
                fenwick.add(query.r + 1, -static_cast<ll>(query.d));
            }
        }

        std::vector<ll> ans(n);
        for (int i = 1; i <= n; ++i) {
            ans[i - 1] = fenwick.prefix_sum(i);
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
