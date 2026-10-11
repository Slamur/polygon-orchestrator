#include "problem_lib.h"

using answer_t = int;
 
void quit_answer(answer_t expected, answer_t actual, InStream & stream) {
    stream.quitf(
        _wa,
        "answer is not best: expected answer %d, found answer %d",
        expected, actual
    );
}
 
bool left_better(answer_t left, answer_t right) {
    return left < right;
}
 
int main(int argc, char * argv[])
{
    registerTestlibCmd(argc, argv);

    // read input
    int n = inf.readInt();
    ll k = inf.readLong();
    vi a = inf.readInts(n);

    auto read_answer = [&](InStream & stream) {
        std::string has_ans = stream.readLine("YES|NO", "yes_no");
        if ("NO" == has_ans) {
            return n + 1;
        }

        int first_gt = stream.readInt(1, n, "first_gt");

        // validating
        ll pref_sum = std::accumulate(a.begin(), a.begin() + first_gt, 0LL);
        stream.quitif(
            pref_sum <= k,
            _wa,
            "Expected pref sum [0; %d) > %lld, but found %lld",
            first_gt, k, pref_sum
        );

        return first_gt;
    };
 
    auto read_answer_from = [&](InStream & stream) {
        answer_t answer = read_answer(stream);
        
        stream.quitif(
            !stream.seekEof(),
            _pe,
            "Extra information in the file"
        );
        
        return answer;
    };

    // read answers (jury and participant)
    answer_t ja = read_answer_from(ans);
    answer_t pa = read_answer_from(ouf);
 
    if (left_better(pa, ja)) quit_answer(ja, pa, ans);
    if (left_better(ja, pa)) quit_answer(ja, pa, ouf);
 
    quitf(_ok, "OK (answer %d)", pa);
}