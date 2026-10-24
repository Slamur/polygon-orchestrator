from collections.abc import Iterator


def read_line() -> str:
    return input()


def read_tokens() -> list[str]:
    return input().split()


def read_ints() -> Iterator[int]:
    return map(int, read_tokens())


def solve():
    n, q = read_ints()
    ls = list(read_ints())
    rs = list(read_ints())
    ds = list(read_ints())

    def get_ans() -> list[int]:
        inc = [0] * (n + 2)

        for l, r, d in zip(ls, rs, ds):
            inc[l] += d
            inc[r + 1] -= d

        ans = [0] * n
        cur = 0
        for i in range(1, n + 1):
            cur += inc[i]
            ans[i - 1] = cur

        return ans

    ans = get_ans()
    for i in range(n):
        print(ans[i], end=' ' if i + 1 < n else '\n')


if __name__ == '__main__':
    solve()
