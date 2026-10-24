from collections.abc import Iterator


def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def read_tokens() -> list[str]:
    return input().split()


def read_ints() -> Iterator[int]:
    return map(int, read_tokens())


def yes_no(yes: bool, yes_str='YES', no_str='NO'):
    print(yes_str if yes else no_str)
    return yes


def solve():
    n, k = read_ints()
    a = list(read_ints())

    def get_ans() -> int:
        prefs = [0] * (n + 1)
        for i, v in enumerate(a):
            prefs[i + 1] = prefs[i] + v

        for i in range(n):
            if prefs[i + 1] > k:
                return i

        return -1

    ans = get_ans()
    if yes_no(ans >= 0):
        print(ans + 1)


if __name__ == '__main__':
    solve()