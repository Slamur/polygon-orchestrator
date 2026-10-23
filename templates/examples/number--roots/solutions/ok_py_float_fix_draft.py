import math


def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def solve():
    n = read_int()

    def get_cube_root(value: int) -> int:
        root = int(value ** (1 / 3))
        while root ** 3 > value:
            root -= 1
        while (root + 1) ** 3 <= value:
            root += 1

        return root

    def get_square_root(value: int) -> int:
        root = int(math.sqrt(value))
        while root ** 2 > value:
            root -= 1
        while (root + 1) ** 2 <= value:
            root += 1

        return root

    cube_root = get_cube_root(n)
    square_root = get_square_root(n)

    print(cube_root, square_root)


if __name__ == '__main__':
    solve()
