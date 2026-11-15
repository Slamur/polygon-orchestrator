import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.Random;
import java.util.StringTokenizer;

public class Solution {
    Random rng = new Random(123456789987654321L);

    int[] randomOrder(int n) {
        int[] order = new int[n];
        for (int i = 0; i < n; i++) {
            order[i] = i + 1;
        }
        for (int i = n - 1; i > 0; i--) {
            int j = rng.nextInt(i + 1);
            int saved = order[i];
            order[i] = order[j];
            order[j] = saved;
        }
        return order;
    }

    String ask(FastInput in, PrintWriter out, int x) {
        out.println("? " + x);
        out.flush();
        return in.readToken();
    }

    void answer(PrintWriter out, int favouriteCount) {
        out.println("! " + favouriteCount);
        out.flush();
    }

    int countFavourites(FastInput in, PrintWriter out, int n) {
        // the order of queries is arbitrary by the statement: ask about the
        // numbers in a random order (a permutation of 1..N)
        int[] order = randomOrder(n);

        int favouriteCount = 0;
        for (int x : order) {
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
