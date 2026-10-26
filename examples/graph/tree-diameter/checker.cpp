#include "problem_lib.h"

// ---------- input tree (shared between jury and participant checks) ----------

int n;
vi par;      // par[i] = parent of i, 0 if i is root (1-indexed)
vi depth_;   // depth_[i] = distance from root to i
int root;

void build_tree(InStream& in) {
    n = in.readInt(1, INT_MAX, "n");

    par.assign(n + 1, 0);
    for (int i = 1; i <= n; i++) {
        par[i] = in.readInt(0, n, "p_i");
    }

    std::vector<vi> children(n + 1);
    root = -1;
    for (int i = 1; i <= n; i++) {
        if (par[i] == 0) {
            root = i;
        } else {
            children[par[i]].push_back(i);
        }
    }

    ensuref(root != -1, "Input tree has no root (no p_i equal to 0)");

    depth_.assign(n + 1, -1);
    std::vector<int> order;
    order.reserve(n);
    order.push_back(root);
    depth_[root] = 0;
    for (size_t qi = 0; qi < order.size(); qi++) {
        int u = order[qi];
        for (int v : children[u]) {
            depth_[v] = depth_[u] + 1;
            order.push_back(v);
        }
    }
}

int lca(int u, int v) {
    while (depth_[u] > depth_[v]) u = par[u];
    while (depth_[v] > depth_[u]) v = par[v];
    while (u != v) {
        u = par[u];
        v = par[v];
    }
    return u;
}

int tree_dist(int u, int v) {
    int l = lca(u, v);
    return depth_[u] + depth_[v] - 2 * depth_[l];
}

// ---------- read_and_check ----------

struct Answer {
    int D, S, E;
};

using answer_t = Answer;

answer_t read_answer(InStream& stream) {
    answer_t a;

    a.D = stream.readInt(0, n - 1, "D");
    a.S = stream.readInt(1, n, "S");
    a.E = stream.readInt(1, n, "E");

    int d = tree_dist(a.S, a.E);
    stream.quitif(
        d != a.D,
        _wa,
        "Distance between S=%d and E=%d equals %d, but reported D=%d",
        a.S, a.E, d, a.D
    );

    return a;
}

answer_t read_answer_from(InStream& stream) {
    answer_t a = read_answer(stream);
    stream.seekEof();
    return a;
}

int main(int argc, char* argv[]) {
    setName("checker for tree diameter problem");
    registerTestlibCmd(argc, argv);

    build_tree(inf);

    answer_t ja = read_answer_from(ans);
    answer_t pa = read_answer_from(ouf);

    if (pa.D > ja.D) {
        quitf(
            _fail,
            "Jury answer has diameter %d, but participant found a longer valid path of length %d (S=%d, E=%d)",
            ja.D, pa.D, pa.S, pa.E
        );
    }

    if (pa.D < ja.D) {
        quitf(
            _wa,
            "Participant's diameter %d is less than the optimal diameter %d",
            pa.D, ja.D
        );
    }

    quitf(_ok, "Diameter %d is correct", pa.D);
}
