def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def ask(x: int) -> str:
    print('?', x, flush=True)
    return read_line()


def solve():
    n = read_int()

    def guess() -> None:
        # BUG: линейный перебор вместо бинарного поиска —
        # уже при N = 12 может потребоваться больше 10 запросов
        for x in range(1, n + 1):
            answer = ask(x)
            if answer == '=':
                return

    guess()


if __name__ == '__main__':
    solve()
