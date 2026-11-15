#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = opt<int>("n");
    int cnt = opt<int>("cnt");

    auto gen_test = [&](int n, int cnt) {
        ensuref(
            1 <= n && n <= 100,
            "Expected n in [1, 100], but found %d",
            n
        );
        ensuref(
            0 <= cnt && cnt <= n,
            "Expected cnt in [0, %d], but found %d",
            n, cnt
        );

        vi nums(n);
        for (int i = 0; i < n; ++i) nums[i] = i + 1;
        shuffle(nums.begin(), nums.end());

        vi f(n, 0);
        for (int i = 0; i < cnt; ++i) {
            f[nums[i] - 1] = 1;
        }

        println(n);
        println(f);
    };

    gen_test(n, cnt);

    return 0;
}
