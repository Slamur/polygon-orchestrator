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
        # TL: linear scan S = 0, 1, 2, ... while S^2 <= value (up to 10^9 steps)
        s = 0
        while (s + 1) ** 2 <= value:
            s += 1

        return s

    cube_root = get_cube_root(n)
    square_root = get_square_root(n)

    print(cube_root, square_root)


if __name__ == '__main__':
    solve()
