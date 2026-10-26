#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    std::vector<int> parent(n + 1);
    for (int v = 1; v <= n; v++) {
        std::cin >> parent[v];
    }

    int root = 1;
    std::vector<std::vector<int>> children(n + 1);
    for (int v = 1; v <= n; v++) {
        if (parent[v] == 0) {
            root = v;
        } else {
            children[parent[v]].push_back(v);
        }
    }

    auto get_ans = [&]() {
        // Iterative top-down order (BFS): a parent always comes before its children.
        std::vector<int> order;
        order.reserve(n);
        order.push_back(root);
        for (size_t i = 0; i < order.size(); i++) {
            int v = order[i];
            for (int to : children[v]) {
                order.push_back(to);
            }
        }

        std::vector<int> height(n + 1, 0);
        std::vector<int> deepest(n + 1);
        for (int v = 1; v <= n; v++) {
            deepest[v] = v;
        }

        int best_d = 0;
        int best_s = root;
        int best_e = root;

        for (int i = n - 1; i >= 0; i--) {
            int v = order[i];

            int first_len = 0;
            int first_end = v;
            int second_len = 0;
            int second_end = v;

            for (int to : children[v]) {
                int len = height[to] + 1;
                int end = deepest[to];
                if (len > first_len) {
                    second_len = first_len;
                    second_end = first_end;
                    first_len = len;
                    first_end = end;
                } else if (len > second_len) {
                    second_len = len;
                    second_end = end;
                }
            }

            height[v] = first_len;
            deepest[v] = first_end;

            if (first_len + second_len > best_d) {
                best_d = first_len + second_len;
                best_s = first_end;
                best_e = second_end;
            }
        }

        return std::array<int, 3>{best_d, best_s, best_e};
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
