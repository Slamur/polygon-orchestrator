// SUGGESTION: tree DP that assumes p_i < i and therefore processes vertices
// in the order N, N-1, ..., 1 instead of a real bottom-up traversal order.
// Passes the sample from the statement, but breaks whenever some parent has
// a greater index than its child.
#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    std::vector<int> parent(n + 1);
    for (int v = 1; v <= n; v++) {
        std::cin >> parent[v];
    }

    auto get_ans = [&]() {
        std::vector<int> first_len(n + 1, 0);
        std::vector<int> second_len(n + 1, 0);
        std::vector<int> first_end(n + 1);
        std::vector<int> second_end(n + 1);
        for (int v = 1; v <= n; v++) {
            first_end[v] = v;
            second_end[v] = v;
        }

        // BUG: the vertices are processed in the order N..1 assuming that the
        // parent index is always smaller than the child index, so a subtree
        // height may be pushed to the parent before it is final.
        for (int v = n; v >= 1; v--) {
            int p = parent[v];
            if (p == 0) {
                continue;
            }
            int len = first_len[v] + 1;
            int end = first_end[v];
            if (len > first_len[p]) {
                second_len[p] = first_len[p];
                second_end[p] = first_end[p];
                first_len[p] = len;
                first_end[p] = end;
            } else if (len > second_len[p]) {
                second_len[p] = len;
                second_end[p] = end;
            }
        }

        int best_d = 0;
        int best_s = 1;
        int best_e = 1;
        for (int v = 1; v <= n; v++) {
            if (first_len[v] + second_len[v] > best_d) {
                best_d = first_len[v] + second_len[v];
                best_s = first_end[v];
                best_e = second_end[v];
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
