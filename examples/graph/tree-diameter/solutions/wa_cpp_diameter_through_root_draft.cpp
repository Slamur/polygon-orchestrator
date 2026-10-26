#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    std::vector<int> parent(n + 1);
    for (int v = 1; v <= n; v++) {
        std::cin >> parent[v];
    }

    std::vector<std::vector<int>> adjacency(n + 1);
    for (int v = 1; v <= n; v++) {
        if (parent[v] != 0) {
            adjacency[v].push_back(parent[v]);
            adjacency[parent[v]].push_back(v);
        }
    }

    std::vector<int> dist(n + 1, -1);
    std::vector<int> branch(n + 1, 0);

    auto bfs_from_root = [&](int start) {
        std::fill(dist.begin(), dist.end(), -1);

        std::vector<int> order;
        order.reserve(n);
        order.push_back(start);
        dist[start] = 0;
        branch[start] = start;

        for (size_t i = 0; i < order.size(); i++) {
            int v = order[i];
            for (int to : adjacency[v]) {
                if (dist[to] == -1) {
                    dist[to] = dist[v] + 1;
                    branch[to] = (v == start ? to : branch[v]);
                    order.push_back(to);
                }
            }
        }
    };

    auto get_ans = [&]() {
        int root = 1;
        for (int v = 1; v <= n; v++) {
            if (parent[v] == 0) {
                root = v;
            }
        }

        bfs_from_root(root);

        std::vector<int> branch_len(n + 1, 0);
        std::vector<int> branch_end(n + 1, root);
        for (int v = 1; v <= n; v++) {
            if (v == root) {
                continue;
            }
            int b = branch[v];
            if (dist[v] > branch_len[b]) {
                branch_len[b] = dist[v];
                branch_end[b] = v;
            }
        }

        // BUG: the diameter is assumed to always pass through the root, so only
        // the two deepest branches of the root are joined; a longer path lying
        // entirely inside one branch is never considered.
        int first_len = 0;
        int first_end = root;
        int second_len = 0;
        int second_end = root;

        for (int to : adjacency[root]) {
            if (branch_len[to] > first_len) {
                second_len = first_len;
                second_end = first_end;
                first_len = branch_len[to];
                first_end = branch_end[to];
            } else if (branch_len[to] > second_len) {
                second_len = branch_len[to];
                second_end = branch_end[to];
            }
        }

        return std::array<int, 3>{first_len + second_len, first_end, second_end};
    };

    auto ans = get_ans();
    std::cout << ans[0] << " " << ans[1] << " " << ans[2] << "\n";
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
