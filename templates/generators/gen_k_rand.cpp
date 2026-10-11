#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	ll minK = opt<ll>("minK");
	ll maxK = opt<ll>("maxK");

	int n = ri();
	vi a = rv<int>(n);

	auto gen_test = [&]() {
		ll k = rnd.next(minK, maxK);
		
		println(n, k);
		println(a);
	};

	gen_test();
}