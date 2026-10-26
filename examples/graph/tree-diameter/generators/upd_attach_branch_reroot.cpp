#include "problem_lib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int k = opt<int>("k");

    auto gen_test = [&]() {
        ensuref(k >= 1, "k must be at least 1, found %d", k);

        int n = ri();
        vi p = rv<int>(n); // p[i - 1] is the parent of vertex i (0 for the root)

        int root = -1;
        for (int v = 1; v <= n; ++v) {
            if (p[v - 1] == 0) root = v;
        }
        ensuref(root != -1, "input tree has no root (no vertex with parent 0)");

        int total = n + k;

        // parent array of the extended tree, still rooted at the original root
        vi parent(total + 1, 0);
        for (int v = 1; v <= n; ++v) parent[v] = p[v - 1];

        // attach a random tree of k new vertices (n+1..n+k) to the existing root,
        // the same way gen_tree_rand attaches new vertices to an already-placed prefix
        vi branch = rnd.perm(k, n + 1); // random order of the new vertex ids
        vi placed = {root};
        for (int i = 0; i < k; ++i) {
            int newV = branch[i];
            int newParent = rnd.any(placed);
            parent[newV] = newParent;
            placed.push_back(newV);
        }

        // pick the new global root uniformly among the attached branch vertices
        int newRoot = rnd.any(branch);

        // build adjacency list of the extended (undirected) tree
        std::vector<vi> adj(total + 1);
        for (int v = 1; v <= total; ++v) {
            if (parent[v] != 0) {
                adj[v].push_back(parent[v]);
                adj[parent[v]].push_back(v);
            }
        }

        // re-root the whole tree at newRoot via an iterative BFS (depth can be up to N)
        vi newParent(total + 1, -1);
        newParent[newRoot] = 0;
        std::vector<int> queue;
        queue.push_back(newRoot);
        for (size_t qi = 0; qi < queue.size(); ++qi) {
            int u = queue[qi];
            for (int w : adj[u]) {
                if (newParent[w] == -1) {
                    newParent[w] = u;
                    queue.push_back(w);
                }
            }
        }

        println(total);
        println(vi(newParent.begin() + 1, newParent.end()));
    };

    gen_test();

    return 0;
}
