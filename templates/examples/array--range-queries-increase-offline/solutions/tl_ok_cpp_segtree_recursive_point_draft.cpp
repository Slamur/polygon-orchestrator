#include <bits/stdc++.h>

using ll = int64_t;

struct Query {
    int l;
    int r;
    int d;
};

// segment tree over the difference array: point add, sum on a prefix
class SegmentTree {
public:
    explicit SegmentTree(int n) : n_(n), sum_(4 * n, 0) {}

    // adds d at the 1-indexed position pos
    void add(int pos, ll d) {
        add(1, 1, n_, pos, d);
    }

    // sum on the 1-indexed prefix [1, pos]
    ll prefix_sum(int pos) const {
        return query(1, 1, n_, 1, pos);
    }

private:
    int n_;
    std::vector<ll> sum_;

    void add(int node, int nl, int nr, int pos, ll d) {
        sum_[node] += d;
        if (nl == nr) {
            return;
        }

        int mid = (nl + nr) / 2;
        if (pos <= mid) {
            add(2 * node, nl, mid, pos, d);
        } else {
            add(2 * node + 1, mid + 1, nr, pos, d);
        }
    }

    ll query(int node, int nl, int nr, int l, int r) const {
        if (r < nl || nr < l) {
            return 0;
        }

        if (l <= nl && nr <= r) {
            return sum_[node];
        }

        int mid = (nl + nr) / 2;
        return query(2 * node, nl, mid, l, r) + query(2 * node + 1, mid + 1, nr, l, r);
    }
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
