import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long getCubeRoot(long value) {
        long lo = 0;
        // BUG: upper bound N instead of 10^6,
        // mid * mid * mid overflows long for N > ~4 * 10^6
        long hi = value;
        while (lo < hi) {
            long mid = lo + (hi - lo + 1) / 2;
            if (mid * mid * mid <= value) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }

        return lo;
    }

    long getSquareRoot(long value) {
        long lo = 0;
        long hi = 1000000000;  // (10^9)^2 = 10^18, no overflow in long
        while (lo < hi) {
            long mid = lo + (hi - lo + 1) / 2;
            if (mid * mid <= value) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }

        return lo;
    }

    void solve(FastInput in, PrintWriter out) {
        long n = in.readLong();

        long cubeRoot = getCubeRoot(n);
        long squareRoot = getSquareRoot(n);

        out.println(cubeRoot + " " + squareRoot);
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
