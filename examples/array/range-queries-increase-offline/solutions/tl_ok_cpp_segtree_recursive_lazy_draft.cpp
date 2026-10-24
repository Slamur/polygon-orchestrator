#include <bits/stdc++.h>

using ll = int64_t;

struct Query {
    int l;
    int r;
    int d;
};

class SegmentTree {
public:
    explicit SegmentTree(int n) : n_(n), add_(4 * n, 0) {}

    void update(int l, int r, ll d) {
        update(1, 1, n_, l, r, d);
    }

    ll query(int pos) {
        return query(1, 1, n_, pos);
    }

private:
    int n_;
    std::vector<ll> add_;

    void update(int node, int nl, int nr, int l, int r, ll d) {
        if (r < nl || nr < l) {
            return;
        }

        if (l <= nl && nr <= r) {
            add_[node] += d;
            return;
        }

        int mid = (nl + nr) / 2;
        update(2 * node, nl, mid, l, r, d);
        update(2 * node + 1, mid + 1, nr, l, r, d);
    }

    ll query(int node, int nl, int nr, int pos) {
        if (nl == nr) {
            return add_[node];
        }

        int mid = (nl + nr) / 2;
        if (pos <= mid) {
            return add_[node] + query(2 * node, nl, mid, pos);
        }

        return add_[node] + query(2 * node + 1, mid + 1, nr, pos);
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
            tree.update(query.l, query.r, query.d);
        }

        std::vector<ll> ans(n);
        for (int i = 1; i <= n; ++i) {
            ans[i - 1] = tree.query(i);
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
