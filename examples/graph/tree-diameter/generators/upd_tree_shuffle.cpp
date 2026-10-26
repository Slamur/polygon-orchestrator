#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    auto gen_test = [&]() {
        int n = ri();
        vi p = rv<int>(n); // p[i - 1] is the parent of vertex i (0 for the root)

        // random relabeling of the n vertices
        vi newLabel = rnd.perm(n, 1);

        vi q(n, 0);
        for (int oldV = 1; oldV <= n; ++oldV) {
            int oldParent = p[oldV - 1];
            int newV = newLabel[oldV - 1];
            q[newV - 1] = (oldParent == 0) ? 0 : newLabel[oldParent - 1];
        }

        println(n);
        println(q);
    };

    gen_test();

    return 0;
}
