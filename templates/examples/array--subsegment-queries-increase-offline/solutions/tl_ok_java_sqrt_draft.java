import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long[] getAns(int n, int q, int[] l, int[] r, int[] d) {
        int block = Math.max(1, (int) Math.sqrt((double) n));
        int blocks = (n + block - 1) / block;

        long[] elem = new long[n];
        long[] lazy = new long[blocks];

        for (int i = 0; i < q; ++i) {
            int lo = l[i] - 1;
            int hi = r[i] - 1;
            int bl = lo / block;
            int br = hi / block;

            if (bl == br) {
                for (int j = lo; j <= hi; ++j) {
                    elem[j] += d[i];
                }
            } else {
                for (int j = lo; j < (bl + 1) * block; ++j) {
                    elem[j] += d[i];
                }
                for (int b = bl + 1; b < br; ++b) {
                    lazy[b] += d[i];
                }
                for (int j = br * block; j <= hi; ++j) {
                    elem[j] += d[i];
                }
            }
        }

        long[] ans = new long[n];
        for (int i = 0; i < n; ++i) {
            ans[i] = elem[i] + lazy[i / block];
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
