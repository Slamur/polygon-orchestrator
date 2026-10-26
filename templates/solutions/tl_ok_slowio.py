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
    # slow output — a separate print call for every element, without join, e.g.
    #     for v in ans:
    #         print(v)  # or print(v, end=' ') for one line
    print(ans)


if __name__ == '__main__':
    solve()
