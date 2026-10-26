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
    # Slow input on purpose: the parent array is converted token by token
    # in a Python-level loop instead of a single map/list conversion.
    parents = []
    for token in read_tokens():
        parents.append(int(token))

    adjacency = [[] for _ in range(n + 1)]
    for v in range(1, n + 1):
        parent = parents[v - 1]
        if parent != 0:
            adjacency[v].append(parent)
            adjacency[parent].append(v)

    def farthest(start: int) -> tuple[int, int]:
        dist = [-1] * (n + 1)
        dist[start] = 0
        order = [start]
        for v in order:
            next_dist = dist[v] + 1
            for to in adjacency[v]:
                if dist[to] == -1:
                    dist[to] = next_dist
                    order.append(to)
        # BFS visits vertices in non-decreasing order of distance,
        # so the last visited vertex is one of the farthest ones.
        best = order[-1]
        return best, dist[best]

    def get_ans() -> tuple[int, int, int]:
        s, _ = farthest(1)
        e, d = farthest(s)
        return d, s, e

    d, s, e = get_ans()
    # Slow output on purpose: a separate print call per value.
    print(d, end=' ')
    print(s, end=' ')
    print(e)


if __name__ == '__main__':
    solve()
