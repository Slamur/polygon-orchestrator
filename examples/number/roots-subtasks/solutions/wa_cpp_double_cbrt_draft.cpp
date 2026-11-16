#include <bits/stdc++.h>

using ll = int64_t;

void solve() {
    ll n;

    std::cin >> n;

    auto get_cube_root = [&](ll value) {
        // BUG: double cbrt truncated without integer correction,
        // off by one on B^3 - 1 for large B
        return (ll)std::cbrt((double)value);
    };

    auto get_square_root = [&](ll value) {
        ll lo = 0;
        ll hi = 1000000000;  // (10^9)^2 = 10^18, no overflow in ll
        while (lo < hi) {
            ll mid = lo + (hi - lo + 1) / 2;
            if (mid * mid <= value) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }

        return lo;
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
