import java.io.IOException;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.io.PrintWriter;
import java.util.StringTokenizer;

public class Solution {
    int[] nodeValue;
    int[] leftChild;
    int[] rightChild;
    int nodeCount;

    int build(int lo, int hi) {
        if (lo > hi) {
            return -1;
        }
        int mid = (lo + hi) / 2;
        int node = nodeCount;
        nodeCount++;
        nodeValue[node] = mid;
        leftChild[node] = build(lo, mid - 1);
        rightChild[node] = build(mid + 1, hi);
        return node;
    }

    String ask(FastInput in, PrintWriter out, int x) {
        out.println("? " + x);
        out.flush();
        return in.readToken();
    }

    void guess(FastInput in, PrintWriter out, int root) {
        int node = root;
        while (node != -1) {
            String answer = ask(in, out, nodeValue[node]);
            if (answer.equals("=")) {
                return;
            }
            if (answer.equals("<")) {
                node = leftChild[node];
            } else {
                node = rightChild[node];
            }
        }
    }

    void solve(FastInput in, PrintWriter out) {
        int n = in.readInt();

        nodeValue = new int[n];
        leftChild = new int[n];
        rightChild = new int[n];
        nodeCount = 0;
        int root = build(1, n);

        guess(in, out, root);
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
