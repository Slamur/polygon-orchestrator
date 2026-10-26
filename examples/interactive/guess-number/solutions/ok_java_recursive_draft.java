import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    String ask(FastInput in, PrintWriter out, int x) {
        out.println("? " + x);
        out.flush();
        return in.readToken();
    }

    void guess(FastInput in, PrintWriter out, int lo, int hi) {
        if (lo > hi) {
            return;
        }
        int mid = (lo + hi) / 2;
        String answer = ask(in, out, mid);
        if (answer.equals("=")) {
            return;
        }
        if (answer.equals("<")) {
            guess(in, out, lo, mid - 1);
        } else {
            guess(in, out, mid + 1, hi);
        }
    }

    void solve(FastInput in, PrintWriter out) {
        int n = in.readInt();

        guess(in, out, 1, n);
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
