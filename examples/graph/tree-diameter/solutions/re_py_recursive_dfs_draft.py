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

    adjacency = [[] for _ in range(n + 1)]
    for v in range(1, n + 1):
        parent = parents[v - 1]
        if parent != 0:
            adjacency[v].append(parent)
            adjacency[parent].append(v)

    dist = [0] * (n + 1)

    # The algorithm is correct, but the traversal is recursive: on a bamboo
    # with N = 3 * 10^5 vertices the recursion depth equals N, so Python
    # fails with RecursionError (stack overflow).
    def dfs(v: int, parent_vertex: int, depth: int) -> None:
        dist[v] = depth
        for to in adjacency[v]:
            if to != parent_vertex:
                dfs(to, v, depth + 1)

    def farthest(start: int) -> tuple[int, int]:
        dfs(start, 0, 0)
        best = start
        for v in range(1, n + 1):
            if dist[v] > dist[best]:
                best = v
        return best, dist[best]

    def get_ans() -> tuple[int, int, int]:
        s, _ = farthest(1)
        e, d = farthest(s)
        return d, s, e

    d, s, e = get_ans()
    print(d, s, e)


if __name__ == '__main__':
    solve()
