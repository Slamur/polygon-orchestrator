def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def ask(x: int) -> str:
    print('?', x)
    return read_line()


def solve():
    n = read_int()

    def guess() -> None:
        lo, hi = 1, n
        while lo <= hi:
            mid = (lo + hi) // 2
            answer = ask(mid)
            if answer == '=':
                return
            if answer == '<':
                hi = mid - 1
            else:
                lo = mid + 1

    guess()


if __name__ == '__main__':
    solve()
