#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");

	auto gen_test = [&]() {
		vi a(n);
		for (auto &v : a) v = rnd.next(1, n);

		println(n);
		println(a);
	};

	gen_test();
}
