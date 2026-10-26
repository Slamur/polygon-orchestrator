#include "testlib.h"
#include <bits/stdc++.h>

const int minN = 2, maxN = 300000;
const int minP = 0;

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int n = inf.readInt(minN, maxN, "N");
    inf.readEoln();

    const int maxP = n;  // p_i <= N

    std::vector<int> p1 = inf.readInts(n, minP, maxP, "p_i");
    inf.readEoln();
    inf.readEof();

    // 1-индексация: p[i] — предок вершины i
    std::vector<int> p(n + 1, 0);
    for (int i = 1; i <= n; ++i) {
        p[i] = p1[i - 1];
    }

    // ровно одно p_i равно 0 — ровно один корень
    int rootCount = 0;
    for (int i = 1; i <= n; ++i) {
        if (p[i] == 0) ++rootCount;
    }
    ensuref(
        rootCount == 1,
        "Expected exactly one root (p_i = 0), but found %d",
        rootCount
    );

    // p_i != i
    for (int i = 1; i <= n; ++i) {
        ensuref(
            p[i] != i,
            "Expected p_%d != %d, but vertex is its own parent",
            i, i
        );
    }

    // массив предков задаёт дерево: из любой вершины по предкам доходим
    // до корня, циклов нет
    const int UNVISITED = 0, IN_PATH = 1, DONE = 2;
    std::vector<int> state(n + 1, UNVISITED);
    std::vector<int> path;
    for (int v = 1; v <= n; ++v) {
        if (state[v] != UNVISITED) continue;

        path.clear();
        int u = v;
        while (u != 0 && state[u] == UNVISITED) {
            state[u] = IN_PATH;
            path.push_back(u);
            u = p[u];
        }

        ensuref(
            u == 0 || state[u] == DONE,
            "Expected parent array to be a tree, but found a cycle through vertex %d",
            u
        );

        for (int x : path) state[x] = DONE;
    }

    return 0;
}
