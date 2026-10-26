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

    auto farthest = [&](int start) {
        std::fill(dist.begin(), dist.end(), -1);

        std::vector<int> order;
        order.reserve(n);
        order.push_back(start);
        dist[start] = 0;

        for (size_t i = 0; i < order.size(); i++) {
            int v = order[i];
            for (int to : adjacency[v]) {
                if (dist[to] == -1) {
                    dist[to] = dist[v] + 1;
                    order.push_back(to);
                }
            }
        }

        int best = start;
        for (int v = 1; v <= n; v++) {
            if (dist[v] > dist[best]) {
                best = v;
            }
        }
        return best;
    };

    auto get_ans = [&]() {
        int s = farthest(1);
        int e = farthest(s);
        // BUG: D is printed as the number of vertices on the path
        // (one more than the number of edges).
        return std::array<int, 3>{dist[e] + 1, s, e};
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
