#include <bits/stdc++.h>

using ll = int64_t;

struct Query {
    int l;
    int r;
    int d;
};

// bottom-up segment tree over the difference array: point add, sum on a prefix
class SegmentTree {
public:
    explicit SegmentTree(int n) : n_(n), tree_(2 * n, 0) {}

    // adds d at the 1-indexed position pos
    void add(int pos, ll d) {
        for (int node = pos - 1 + n_; node >= 1; node >>= 1) {
            tree_[node] += d;
        }
    }

    // sum on the 1-indexed prefix [1, pos]
    ll prefix_sum(int pos) const {
        ll res = 0;
        int lo = n_;
        int hi = pos + n_;
        while (lo < hi) {
            if (lo & 1) {
                res += tree_[lo++];
            }
            if (hi & 1) {
                res += tree_[--hi];
            }
            lo >>= 1;
            hi >>= 1;
        }

        return res;
    }

private:
    int n_;
    std::vector<ll> tree_;
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
        SegmentTree tree(n);
        for (const auto& query : queries) {
            tree.add(query.l, query.d);
            if (query.r + 1 <= n) {
                tree.add(query.r + 1, -static_cast<ll>(query.d));
            }
        }

        std::vector<ll> ans(n);
        for (int i = 1; i <= n; ++i) {
            ans[i - 1] = tree.prefix_sum(i);
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
