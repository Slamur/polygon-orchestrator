import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.StringTokenizer;

public class Solution {
    int n;
    int root;
    List<List<Integer>> children;

    int[] getAns() {
        // Iterative top-down order (BFS): a parent always comes before its children.
        int[] order = new int[n];
        int size = 0;
        order[size++] = root;
        for (int i = 0; i < size; i++) {
            int v = order[i];
            for (int to : children.get(v)) {
                order[size++] = to;
            }
        }

        int[] height = new int[n + 1];
        int[] deepest = new int[n + 1];
        for (int v = 1; v <= n; v++) {
            deepest[v] = v;
        }

        int bestD = 0;
        int bestS = root;
        int bestE = root;

        for (int i = n - 1; i >= 0; i--) {
            int v = order[i];

            int firstLen = 0;
            int firstEnd = v;
            int secondLen = 0;
            int secondEnd = v;

            for (int to : children.get(v)) {
                int len = height[to] + 1;
                int end = deepest[to];
                if (len > firstLen) {
                    secondLen = firstLen;
                    secondEnd = firstEnd;
                    firstLen = len;
                    firstEnd = end;
                } else if (len > secondLen) {
                    secondLen = len;
                    secondEnd = end;
                }
            }

            height[v] = firstLen;
            deepest[v] = firstEnd;

            if (firstLen + secondLen > bestD) {
                bestD = firstLen + secondLen;
                bestS = firstEnd;
                bestE = secondEnd;
            }
        }

        return new int[] {bestD, bestS, bestE};
    }

    void solve(FastInput in, PrintWriter out) {
        n = in.readInt();

        children = new ArrayList<>();
        for (int v = 0; v <= n; v++) {
            children.add(new ArrayList<>());
        }
        root = 1;
        for (int v = 1; v <= n; v++) {
            int parent = in.readInt();
            if (parent == 0) {
                root = v;
            } else {
                children.get(parent).add(v);
            }
        }

        int[] ans = getAns();
        out.println(ans[0] + " " + ans[1] + " " + ans[2]);
    }

    public static void main(String[] args) {
        FastInput in = new FastInput(new InputStreamReader(System.in));
        PrintWriter out = new PrintWriter(System.out);

        new Solution().solve(in, out);

        out.flush();
    }

    static class FastInput {
        BufferedReader in;
        StringTokenizer tok;

        FastInput(InputStreamReader reader) {
            this.in = new BufferedReader(reader);
            this.tok = new StringTokenizer("");
        }

        String readLine() {
            try {
                return in.readLine();
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }

        String readToken() {
            while (!tok.hasMoreTokens()) {
                String nextLine = readLine();
                tok = new StringTokenizer(nextLine);
            }

            return tok.nextToken();
        }

        int readInt() {
            return Integer.parseInt(readToken());
        }

        long readLong() {
            return Long.parseLong(readToken());
        }
    }
}
