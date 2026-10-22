#include <bits/stdc++.h>

using ll = int64_t;

void solve() {
    ll n;

    std::cin >> n;

    // BUG: roots are taken from double sqrt/cbrt and truncated without any
    // integer correction (+-1), so for large N the answer may be off by one
    auto get_cube_root = [&](ll value) {
        return (ll)std::cbrt((double)value);
    };

    auto get_square_root = [&](ll value) {
        return (ll)std::sqrt((double)value);
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
