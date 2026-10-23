def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def solve():
    n = read_int()

    def get_cube_root(value: int) -> int:
        lo, hi = 0, 10 ** 6
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if mid ** 3 <= value:
                lo = mid
            else:
                hi = mid - 1

        return lo

    def get_square_root(value: int) -> int:
        lo, hi = 0, 10 ** 9
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if mid ** 2 <= value:
                lo = mid
            else:
                hi = mid - 1

        return lo

    cube_root = get_cube_root(n)
    square_root = get_square_root(n)

    print(cube_root, square_root)


if __name__ == '__main__':
    solve()
