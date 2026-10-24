import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long[] getAns(int n, int q, int[] l, int[] r, int[] d) {
        long[] inc = new long[n + 2];
        for (int i = 0; i < q; ++i) {
            inc[l[i]] += d[i];
            inc[r[i] + 1] -= d[i];
        }

        long[] ans = new long[n];
        long cur = 0;
        for (int i = 1; i <= n; ++i) {
            cur += inc[i];
            ans[i - 1] = cur;
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
