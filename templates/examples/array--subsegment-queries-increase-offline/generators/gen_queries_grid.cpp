#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");
	int q = opt<int>("q");

	int start = opt<int>("start");
	int end = opt<int>("end");

	int minD = opt<int>("minD");
	int maxD = opt<int>("maxD");

	auto gen_test = [&]() {
		int k = 0;
		while ((k + 1) * (k + 1) <= q) {
			++k;
		}
		ensuref(k >= 1, "Expected q at least 1, but found %d", q);

		int leftFrom = start;
		int leftTo = start + k - 1;
		int rightFrom = end - k + 1;
		int rightTo = end;

		ensure_limits("leftFrom", leftFrom, 1, n);
		ensure_limits("leftTo", leftTo, 1, n);
		ensure_limits("rightFrom", rightFrom, 1, n);
		ensure_limits("rightTo", rightTo, 1, n);
		ensuref(
			leftTo <= rightFrom,
			"Expected every left <= every right (leftTo <= rightFrom), but found leftTo=%d, rightFrom=%d",
			leftTo, rightFrom
		);

		vii segs;
		for (int l = leftFrom; l <= leftTo; ++l) {
			for (int r = rightFrom; r <= rightTo; ++r) {
				segs.push_back({l, r});
			}
		}
		shuffle(segs.begin(), segs.end()); // from testlib.h

		print_test(n, segs, minD, maxD);
	};

	gen_test();
}
