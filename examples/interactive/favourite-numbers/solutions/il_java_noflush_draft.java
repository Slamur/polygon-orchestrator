import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    String ask(FastInput in, PrintWriter out, int x) {
        out.println("? " + x);
        return in.readToken();
    }

    void answer(PrintWriter out, int favouriteCount) {
        out.println("! " + favouriteCount);
        out.flush();
    }

    int countFavourites(FastInput in, PrintWriter out, int n) {
        int favouriteCount = 0;
        for (int x = 1; x <= n; x++) {
            if (ask(in, out, x).equals("YES")) {
                favouriteCount++;
            }
        }
        return favouriteCount;
    }

    void solve(FastInput in, PrintWriter out) {
        int n = in.readInt();

        answer(out, countFavourites(in, out, n));
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
