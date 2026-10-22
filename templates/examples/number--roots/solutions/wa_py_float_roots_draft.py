def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def solve():
    n = read_int()

    # BUG: floating point roots int(n ** 0.5) / int(n ** (1 / 3)) without
    # integer correction lose precision for large n
    def get_cube_root(value: int) -> int:
        return int(value ** (1 / 3))

    def get_square_root(value: int) -> int:
        return int(value ** 0.5)

    cube_root = get_cube_root(n)
    square_root = get_square_root(n)

    print(cube_root, square_root)


if __name__ == '__main__':
    solve()
