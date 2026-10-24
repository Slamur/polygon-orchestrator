#include "problem_lib.h"

// Not described directly in generator_ideas; added (status: proposed, see
// response notes) to realize the specific_test_idea "много операций с
// R = N" and to target the known wrong approach that writes inc[R] -= D
// instead of inc[R + 1] -= D: R is fixed for every operation, L is random.
int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	int n = opt<int>("n");
	int q = opt<int>("q");

	int r = opt<int>("r");

	int minD = opt<int>("minD");
	int maxD = opt<int>("maxD");

	auto gen_test = [&]() {
		ensure_limits("r", r, 1, n);

		vii segs(q);
		for (auto& seg : segs) {
			seg = {rnd.next(1, r), r};
		}

		print_test(n, segs, minD, maxD);
	};

	gen_test();
}
