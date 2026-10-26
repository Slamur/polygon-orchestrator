def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def ask(x: int) -> str:
    print('?', x, flush=True)
    return read_line()


def solve():
    n = read_int()

    def guess(lo: int, hi: int) -> None:
        if lo > hi:
            return
        mid = (lo + hi) // 2
        answer = ask(mid)
        if answer == '=':
            return
        if answer == '<':
            guess(lo, mid - 1)
        else:
            guess(mid + 1, hi)

    guess(1, n)


if __name__ == '__main__':
    solve()
