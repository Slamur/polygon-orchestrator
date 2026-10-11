import java.io.IOException;
import java.io.PrintStream;
import java.util.Scanner;

public class Solution {
    void yesNo(PrintStream out, boolean yes, String yesStr, String noStr) {
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

    void solve(Scanner in, PrintStream out) {
        int n = in.nextInt();
        long k = in.nextLong();

        int[] a = new int[n];
        for (int i = 0; i < n; ++i) {
            a[i] = in.nextInt();
        }

        int ans = getAns(n, k, a);
        if (yesNo(out, ans >= 0, "YES", "NO")) {
            out.println(ans + 1);
        }
    }

    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        PrintStream out = System.out;

        new Solution().solve(in, out);
    }
}
