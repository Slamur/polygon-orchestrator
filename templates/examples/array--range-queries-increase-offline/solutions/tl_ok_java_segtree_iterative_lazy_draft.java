import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long[] getAns(int n, int q, int[] l, int[] r, int[] d) {
        SegmentTree tree = new SegmentTree(n);
        for (int i = 0; i < q; ++i) {
            tree.update(l[i] - 1, r[i] - 1, d[i]);
        }

        long[] ans = new long[n];
        for (int i = 0; i < n; ++i) {
            ans[i] = tree.query(i);
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
        long[] tree;

        SegmentTree(int n) {
            this.n = n;
            this.tree = new long[2 * n];
        }

        // adds d on the 0-indexed segment [l, r]
        void update(int l, int r, long d) {
            int lo = l + n;
            int hi = r + n + 1;
            while (lo < hi) {
                if ((lo & 1) != 0) {
                    tree[lo++] += d;
                }
                if ((hi & 1) != 0) {
                    tree[--hi] += d;
                }
                lo >>= 1;
                hi >>= 1;
            }
        }

        long query(int pos) {
            long res = 0;
            for (int node = pos + n; node >= 1; node >>= 1) {
                res += tree[node];
            }

            return res;
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
