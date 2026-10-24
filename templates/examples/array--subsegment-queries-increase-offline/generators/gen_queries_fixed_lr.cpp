#include "problem_lib.h"

// Not described directly in generator_ideas; added (status: proposed, see
// response notes) to realize the specific_test_ideas "все операции на
// [1, N]" / "[N, N]" / "[1, 1]": every one of Q operations uses exactly the
// same fixed segment (l, r), only D is randomized per operation.
int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");
	int q = opt<int>("q");

	int l = opt<int>("l");
	int r = opt<int>("r");

	int minD = opt<int>("minD");
	int maxD = opt<int>("maxD");

	auto gen_test = [&]() {
		ensure_limits("l", l, 1, n);
		ensure_limits("r", r, l, n);

		vii segs(q, {l, r});

		print_test(n, segs, minD, maxD);
	};

	gen_test();
}
