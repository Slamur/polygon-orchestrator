def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def ask(x: int) -> str:
    print('?', x, flush=True)
    return read_line()


def solve():
    n = read_int()

    node_value: list[int] = []
    left_child: list[int] = []
    right_child: list[int] = []

    def build(lo: int, hi: int) -> int:
        if lo > hi:
            return -1
        mid = (lo + hi) // 2
        node = len(node_value)
        node_value.append(mid)
        left_child.append(-1)
        right_child.append(-1)
        left_child[node] = build(lo, mid - 1)
        right_child[node] = build(mid + 1, hi)
        return node

    root = build(1, n)

    def guess() -> None:
        node = root
        while node != -1:
            answer = ask(node_value[node])
            if answer == '=':
                return
            if answer == '<':
                node = left_child[node]
            else:
                node = right_child[node]

    guess()


if __name__ == '__main__':
    solve()
