#include <bits/stdc++.h>

using ll = int64_t;

void solve() {
    ll n;

    std::cin >> n;

    auto get_cube_root = [&](ll value) {
        ll root = (ll)std::cbrt((long double)value);
        while (root * root * root > value) {
            --root;
        }
        while ((root + 1) * (root + 1) * (root + 1) <= value) {
            ++root;
        }

        return root;
    };

    auto get_square_root = [&](ll value) {
        ll root = (ll)std::sqrt((long double)value);
        while (root * root > value) {
            --root;
        }
        while ((root + 1) * (root + 1) <= value) {
            ++root;
        }

        return root;
    };

    ll cube_root = get_cube_root(n);
    ll square_root = get_square_root(n);

    std::cout << cube_root << " " << square_root << "\n";
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    solve();

    return 0;
}
