import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    long getCubeRoot(long value) {
        long root = (long) Math.cbrt((double) value);
        while (root * root * root > value) {
            --root;
        }
        while ((root + 1) * (root + 1) * (root + 1) <= value) {
            ++root;
        }

        return root;
    }

    long getSquareRoot(long value) {
        long root = (long) Math.sqrt((double) value);
        while (root * root > value) {
            --root;
        }
        while ((root + 1) * (root + 1) <= value) {
            ++root;
        }

        return root;
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
