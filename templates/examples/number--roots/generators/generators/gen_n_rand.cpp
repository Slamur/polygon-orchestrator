#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	ll minN = opt<ll>("minN");
	ll maxN = opt<ll>("maxN");

	auto gen_test = [&]() {
		ll n = rnd.next(minN, maxN);

		println(n);
	};

	gen_test();
}
