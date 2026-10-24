#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	const ll b = opt<ll>("b");
	const int p = opt<int>("p");

	auto gen_test = [&]() {
		ll n = 1;
		for (int i = 0; i < p; ++i) {
			ensuref(n <= LLONG_MAX / b, "gen_n_pow_of_base: %lld^%d overflows ll", b, p);
			n *= b;
		}

		ll delta = rnd.next(-1, 1);
		println(n + delta);
	};

	gen_test();
}
