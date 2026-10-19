#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");

	int minA = opt<int>("minA");
	int maxA = opt<int>("maxA");

	auto gen_test = [&]() {
		vi a = rnd.distinct(n, minA, maxA);
		shuffle(a.begin(), a.end()); // from testlib.h

		println(n);
		println(a);
	};

	gen_test();
}