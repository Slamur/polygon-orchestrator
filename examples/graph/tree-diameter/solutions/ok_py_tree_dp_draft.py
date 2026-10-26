from collections.abc import Iterator


def read_line() -> str:
    return input()


def read_int() -> int:
    return int(read_line())


def read_tokens() -> list[str]:
    return input().split()


def read_ints() -> Iterator[int]:
    return map(int, read_tokens())


def solve():
    n = read_int()
    parents = list(read_ints())

    root = 1
    children = [[] for _ in range(n + 1)]
    for v in range(1, n + 1):
        parent = parents[v - 1]
        if parent == 0:
            root = v
        else:
            children[parent].append(v)

    def get_ans() -> tuple[int, int, int]:
        # Iterative top-down order (BFS): a parent always comes before its children.
        order = [root]
        for v in order:
            for to in children[v]:
                order.append(to)

        height = [0] * (n + 1)
        deepest = list(range(n + 1))

        best_d = 0
        best_s = root
        best_e = root

        for i in range(n - 1, -1, -1):
            v = order[i]

            first_len = 0
            first_end = v
            second_len = 0
            second_end = v

            for to in children[v]:
                length = height[to] + 1
                end = deepest[to]
                if length > first_len:
                    second_len = first_len
                    second_end = first_end
                    first_len = length
                    first_end = end
                elif length > second_len:
                    second_len = length
                    second_end = end

            height[v] = first_len
            deepest[v] = first_end

            if first_len + second_len > best_d:
                best_d = first_len + second_len
                best_s = first_end
                best_e = second_end

        return best_d, best_s, best_e

    d, s, e = get_ans()
    print(d, s, e)


if __name__ == '__main__':
    solve()
