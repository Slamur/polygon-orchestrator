#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	ll maxK = opt<ll>("maxK");

	int n = ri();
	vi a = rv<int>(n);

	auto gen_test = [&]() {
		vll prefs(n + 1, 0);
		for (int i = 0; i < n; ++i) {
			prefs[i + 1] = prefs[i] + a[i];
		}

		ll minK = prefs.back();
		ll k = rnd.next(minK, maxK);
		
		println(n, k);
		println(a);
	};

	gen_test();
}