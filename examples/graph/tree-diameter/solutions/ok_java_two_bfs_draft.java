import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.StringTokenizer;

public class Solution {
    int n;
    List<List<Integer>> adjacency;
    int[] dist;
    int[] order;

    int farthest(int start) {
        for (int v = 0; v <= n; v++) {
            dist[v] = -1;
        }

        int size = 0;
        order[size++] = start;
        dist[start] = 0;

        for (int i = 0; i < size; i++) {
            int v = order[i];
            for (int to : adjacency.get(v)) {
                if (dist[to] == -1) {
                    dist[to] = dist[v] + 1;
                    order[size++] = to;
                }
            }
        }

        int best = start;
        for (int v = 1; v <= n; v++) {
            if (dist[v] > dist[best]) {
                best = v;
            }
        }
        return best;
    }

    int[] getAns() {
        int s = farthest(1);
        int e = farthest(s);
        return new int[] {dist[e], s, e};
    }

    void solve(FastInput in, PrintWriter out) {
        n = in.readInt();

        adjacency = new ArrayList<>();
        for (int v = 0; v <= n; v++) {
            adjacency.add(new ArrayList<>());
        }
        for (int v = 1; v <= n; v++) {
            int parent = in.readInt();
            if (parent != 0) {
                adjacency.get(v).add(parent);
                adjacency.get(parent).add(v);
            }
        }

        dist = new int[n + 1];
        order = new int[n + 1];

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
