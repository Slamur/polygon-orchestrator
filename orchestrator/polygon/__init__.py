"""Integration with the Polygon API (https://polygon.codeforces.com/api).

Everything Polygon-specific (request signing, the HTTP client, reading
`POLYGON_API_KEY`/`POLYGON_API_SECRET`, the state of the `problem_id` ->
Polygon problemId binding) lives only here — not in the orchestrator's shared logic.

Nothing from here is imported at the package's top level: a module that doesn't
work with Polygon doesn't pull in the `requests` dependency.
"""
