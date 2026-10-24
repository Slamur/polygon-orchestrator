#include <bits/stdc++.h>

using ll = int64_t;

struct Query {
    int l;
    int r;
    int d;
};

class SegmentTree {
public:
    explicit SegmentTree(int n) : n_(n), tree_(2 * n, 0) {}

    // adds d on the 0-indexed segment [l, r]
    void update(int l, int r, ll d) {
        int lo = l + n_;
        int hi = r + n_ + 1;
        while (lo < hi) {
            if (lo & 1) {
                tree_[lo++] += d;
            }
            if (hi & 1) {
                tree_[--hi] += d;
            }
            lo >>= 1;
            hi >>= 1;
        }
    }

    ll query(int pos) const {
        ll res = 0;
        for (int node = pos + n_; node >= 1; node >>= 1) {
            res += tree_[node];
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
            tree.update(query.l - 1, query.r - 1, query.d);
        }

        std::vector<ll> ans(n);
        for (int i = 0; i < n; ++i) {
            ans[i] = tree.query(i);
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
