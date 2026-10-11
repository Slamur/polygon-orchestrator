#include "problem_lib.h"

int main(int argc, char **argv) {
	registerGen(argc, argv, 1);

	bool desc = opt<bool>("desc");

	int n = ri();
	ll k = rll();
	vi a = rv<int>(n);

	auto gen_test = [&]() {
		std::sort(a.begin(), a.end());
		if (desc) {
			std::reverse(a.begin(), a.end());
		}
		
		println(n, k);
		println(a);
	};

	gen_test();
}