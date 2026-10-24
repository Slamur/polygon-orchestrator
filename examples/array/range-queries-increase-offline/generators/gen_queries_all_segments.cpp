#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");

	int minD = opt<int>("minD");
	int maxD = opt<int>("maxD");

	auto gen_test = [&]() {
		vii segs;
		for (int l = 1; l <= n; ++l) {
			for (int r = l; r <= n; ++r) {
				segs.push_back({l, r});
			}
		}
		shuffle(segs.begin(), segs.end()); // from testlib.h

		print_test(n, segs, minD, maxD);
	};

	gen_test();
}
