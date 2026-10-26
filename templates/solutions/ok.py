from collections.abc import Iterator


def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def read_tokens() -> list[str]:
    return input().split()


def read_ints() -> Iterator[int]:
    return map(int, read_tokens())


def solve():
    # read input

    def get_ans() -> int:
        # compute and return the answer
        return 0

    ans = get_ans()
    # Fixed small number of values (one number, a pair, three numbers):
    # a plain print, e.g. print(a, b, c) — no join and no per-value prints.
    # Sequence whose length depends on the input (N elements, Q answers):
    # fast output — convert to strings, join into a separate variable and
    # print it in one call, e.g.
    #     ans_str = ' '.join(map(str, ans))  # or '\n'.join(...) for one per line
    #     print(ans_str)
    print(ans)


if __name__ == '__main__':
    solve()
