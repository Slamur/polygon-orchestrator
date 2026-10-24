import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long[] getAns(int n, int q, int[] l, int[] r, int[] d) {
        Fenwick fenwick = new Fenwick(n);
        for (int i = 0; i < q; ++i) {
            fenwick.add(l[i], d[i]);
            if (r[i] + 1 <= n) {
                fenwick.add(r[i] + 1, -(long) d[i]);
            }
        }

        long[] ans = new long[n];
        for (int i = 1; i <= n; ++i) {
            ans[i - 1] = fenwick.prefixSum(i);
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

    static class Fenwick {
        int n;
        long[] bit;

        Fenwick(int n) {
            this.n = n;
            this.bit = new long[n + 1];
        }

        void add(int pos, long d) {
            for (; pos <= n; pos += pos & -pos) {
                bit[pos] += d;
            }
        }

        long prefixSum(int pos) {
            long res = 0;
            for (; pos > 0; pos -= pos & -pos) {
                res += bit[pos];
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
