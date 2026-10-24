import java.io.PrintStream;
import java.util.Scanner;

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

    void solve(Scanner in, PrintStream out) {
        int n = in.nextInt();
        int q = in.nextInt();

        int[] l = new int[q];
        int[] r = new int[q];
        int[] d = new int[q];
        for (int i = 0; i < q; ++i) {
            l[i] = in.nextInt();
        }
        for (int i = 0; i < q; ++i) {
            r[i] = in.nextInt();
        }
        for (int i = 0; i < q; ++i) {
            d[i] = in.nextInt();
        }

        long[] ans = getAns(n, q, l, r, d);
        for (int i = 0; i < n; ++i) {
            out.print(ans[i]);
            out.print(i + 1 < n ? ' ' : '\n');
        }
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        PrintStream out = System.out;

        new Solution().solve(in, out);
    }
}
