#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");
	int q = opt<int>("q");

	int minD = opt<int>("minD");
	int maxD = opt<int>("maxD");

	auto gen_test = [&]() {
		vii segs(q);
		for (auto& seg : segs) {
			int l = rnd.next(1, n);
			int r = rnd.next(1, n);
			if (l > r) {
				std::swap(l, r);
			}

			seg = {l, r};
		}

		print_test(n, segs, minD, maxD);
	};

	gen_test();
}
