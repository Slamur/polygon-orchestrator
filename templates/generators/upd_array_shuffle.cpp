#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = ri();
	ll k = rll();
	vi a = rv<int>(n);

	auto gen_test = [&]() {
		shuffle(a.begin(), a.end()); // from testlib
		
		println(n, k);
		println(a);
	};

	gen_test();
}