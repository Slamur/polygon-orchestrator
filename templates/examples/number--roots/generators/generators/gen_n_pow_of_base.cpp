#include "problem_lib.h"

namespace {

ll power_value(ll base, int exp) {
	__int128 result = 1;
	for (int i = 0; i < exp; ++i) {
		result *= base;
	}
	return (ll)result;
}

}

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	ll b = opt<ll>("b");
	int p = opt<int>("p");
	ll delta = opt<ll>("delta");

	ll minN = opt<ll>("minN");
	ll maxN = opt<ll>("maxN");

	auto gen_test = [&]() {
		ll n = power_value(b, p) + delta;

		ensuref(
			n >= minN && n <= maxN,
			"gen_n_pow_of_base: %lld^%d + %lld = %lld is out of [%lld, %lld]",
			b, p, delta, n, minN, maxN
		);

		println(n);
	};

	gen_test();
}
