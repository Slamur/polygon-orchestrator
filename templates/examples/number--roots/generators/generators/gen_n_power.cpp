#include "problem_lib.h"

namespace {

bool power_le(ll base, int exp, ll limit) {
	__int128 result = 1;
	for (int i = 0; i < exp; ++i) {
		result *= base;
		if (result > limit) return false;
	}
	return true;
}

ll power_value(ll base, int exp) {
	__int128 result = 1;
	for (int i = 0; i < exp; ++i) {
		result *= base;
	}
	return (ll)result;
}

// largest B such that B^exp <= limit, found by binary search on the answer
// (so it stays correct for any exp/limit passed through params, no hardcoded bound)
ll max_base_for_power(int exp, ll limit) {
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

}

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int k = opt<int>("k");

	ll minN = opt<ll>("minN");
	ll maxN = opt<ll>("maxN");

	ll minB = opt<ll>("minB");
	ll defaultMaxB = max_base_for_power(k, maxN);
	ll maxB = has_opt("maxB") ? opt<ll>("maxB") : defaultMaxB;

	ll delta = opt<ll>("delta");

	auto gen_test = [&]() {
		const int MAX_ATTEMPTS = 1000;
		ll n = -1;

		for (int attempt = 0; attempt < MAX_ATTEMPTS; ++attempt) {
			ll b = rnd.next(minB, maxB);
			ll value = power_value(b, k) + delta;
			if (value >= minN && value <= maxN) {
				n = value;
				break;
			}
		}

		ensuref(
			n != -1,
			"gen_n_power: failed to find B in [%lld, %lld] with B^%d + %lld landing in [%lld, %lld]",
			minB, maxB, k, delta, minN, maxN
		);

		println(n);
	};

	gen_test();
}
