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
    # Slow output: a separate print call for every value, without joining, e.g.
    # for v in ans: print(v)  or, for one line, print(v, end=' ') per value.
    print(ans)


if __name__ == '__main__':
    solve()
