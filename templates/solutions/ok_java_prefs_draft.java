import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;

public class Solution {
    void yesNo(PrintWriter out, boolean yes, String yesStr, String noStr) {
        out.println(yes ? yesStr : noStr);
        return yes;
    }

    int getAns(int n, long k, int[] a) {
        long[] prefs = new long[n + 1];
        for (int i = 0; i < n; ++i) {
            prefs[i + 1] = prefs[i] + a[i];
        }

        for (int i = 0; i < n; ++i) {
            if (prefs[i + 1] > k) return i;
        }

        return -1;
    }

    void solve(FastInput in, PrintWriter out) {
        int n = in.readInt();
        long k = in.readLong();

        int[] a = new int[n];
        for (int i = 0; i < n; ++i) {
            a[i] = in.readInt();
        }

        int ans = getAns(n, k, a);
        if (yesNo(out, ans >= 0)) {
            out.println(ans + 1);
        }
    }

    public static void main(String[] args) throws IOException {
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

        String readToken() {
            while (!tok.hasMoreTokens()) {
                String nextLine = in.readLine();
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
