import random


def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def ask(x: int) -> str:
    print('?', x, flush=True)
    return read_line()


def answer(favourite_count: int) -> None:
    print('!', favourite_count, flush=True)


def solve():
    n = read_int()
    random.seed(123456789987654321)

    def count_favourites() -> int:
        # the order of queries is arbitrary by the statement: ask about the
        # numbers in a random order (a permutation of 1..N)
        order = list(range(1, n + 1))
        random.shuffle(order)

        favourite_count = 0
        for x in order:
            if ask(x) == 'YES':
                favourite_count += 1
        return favourite_count

    answer(count_favourites())


if __name__ == '__main__':
    solve()
