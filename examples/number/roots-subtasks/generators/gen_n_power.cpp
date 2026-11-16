#include "problem_lib.h"

namespace {

// base^exp <= limit, without overflow
bool power_le(ll base, int exp, ll limit) {
	ll result = 1;
	for (int i = 0; i < exp; ++i) {
		if (result > limit / base) return false;
		result *= base;
	}
	return true;
}

// largest B >= 1 such that B^exp <= limit
ll max_base(int exp, ll limit) {
	ll lo = 1, hi = limit;
	while (lo < hi) {
		ll mid = lo + (hi - lo + 1) / 2;
		if (power_le(mid, exp, limit)) {
			lo = mid;
		} else {
			hi = mid - 1;
		}
	}
	return lo;
}

ll power(ll base, int exp) {
	ll result = 1;
	for (int i = 0; i < exp; ++i) {
		result *= base;
	}
	return result;
}

}

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	const int k = opt<int>("k");
	const ll maxN = opt<ll>("maxN");

	const ll minB = opt<ll>("minB");
	const ll delta = opt<ll>("delta");

	auto gen_test = [&]() {
		// B^k + delta <= maxN
		ll maxB = max_base(k, maxN - delta);
		ensuref(minB <= maxB, "gen_n_power: minB = %lld > maxB = %lld for k = %d, delta = %lld", minB, maxB, k, delta);

		ll b = rnd.next(minB, maxB);
		println(power(b, k) + delta);
	};

	gen_test();
}
