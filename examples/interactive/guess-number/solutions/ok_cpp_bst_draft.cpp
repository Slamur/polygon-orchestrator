#include <bits/stdc++.h>

void solve() {
    int n;
    std::cin >> n;

    std::vector<int> node_value;
    std::vector<int> left_child;
    std::vector<int> right_child;

    std::function<int(int, int)> build = [&](int lo, int hi) {
        if (lo > hi) {
            return -1;
        }
        int mid = (lo + hi) / 2;
        int node = static_cast<int>(node_value.size());
        node_value.push_back(mid);
        left_child.push_back(-1);
        right_child.push_back(-1);
        left_child[node] = build(lo, mid - 1);
        right_child[node] = build(mid + 1, hi);
        return node;
    };

    int root = build(1, n);

    auto ask = [&](int x) {
        std::cout << "? " << x << std::endl;
        std::string answer;
        std::cin >> answer;
        return answer;
    };

    auto guess = [&]() {
        int node = root;
        while (node != -1) {
            std::string answer = ask(node_value[node]);
            if (answer == "=") {
                return;
            }
            if (answer == "<") {
                node = left_child[node];
            } else {
                node = right_child[node];
            }
        }
    };

    guess();
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
