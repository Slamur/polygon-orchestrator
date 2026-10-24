import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long[] getAns(int n, int q, int[] l, int[] r, int[] d) {
        SegmentTree tree = new SegmentTree(n);
        for (int i = 0; i < q; ++i) {
            tree.update(l[i], r[i], d[i]);
        }

        long[] ans = new long[n];
        for (int i = 1; i <= n; ++i) {
            ans[i - 1] = tree.query(i);
        }

        return ans;
    }

    void solve(FastInput in, PrintWriter out) {
        int n = in.readInt();
        int q = in.readInt();

        int[] l = new int[q];
        int[] r = new int[q];
        int[] d = new int[q];
        for (int i = 0; i < q; ++i) {
            l[i] = in.readInt();
        }
        for (int i = 0; i < q; ++i) {
            r[i] = in.readInt();
        }
        for (int i = 0; i < q; ++i) {
            d[i] = in.readInt();
        }

        long[] ans = getAns(n, q, l, r, d);
        for (int i = 0; i < n; ++i) {
            out.print(ans[i]);
            out.print(i + 1 < n ? ' ' : '\n');
        }
    }

    public static void main(String[] args) {
        FastInput in = new FastInput(new InputStreamReader(System.in));
        PrintWriter out = new PrintWriter(System.out);

        new Solution().solve(in, out);

        out.flush();
    }

    static class SegmentTree {
        int n;
        long[] add;

        SegmentTree(int n) {
            this.n = n;
            this.add = new long[4 * n];
        }

        void update(int l, int r, long d) {
            update(1, 1, n, l, r, d);
        }

        long query(int pos) {
            return query(1, 1, n, pos);
        }

        void update(int node, int nl, int nr, int l, int r, long d) {
            if (r < nl || nr < l) {
                return;
            }

            if (l <= nl && nr <= r) {
                add[node] += d;
                return;
            }

            int mid = (nl + nr) / 2;
            update(2 * node, nl, mid, l, r, d);
            update(2 * node + 1, mid + 1, nr, l, r, d);
        }

        long query(int node, int nl, int nr, int pos) {
            if (nl == nr) {
                return add[node];
            }

            int mid = (nl + nr) / 2;
            if (pos <= mid) {
                return add[node] + query(2 * node, nl, mid, pos);
            }

            return add[node] + query(2 * node + 1, mid + 1, nr, pos);
        }
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
    }
}
