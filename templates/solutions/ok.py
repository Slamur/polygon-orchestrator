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
    # Fast output: the whole answer in one print call. Several values are
    # converted to strings and joined first, e.g.
    # print(' '.join(map(str, ans))) or print('\n'.join(map(str, ans))).
    print(ans)


if __name__ == '__main__':
    solve()
