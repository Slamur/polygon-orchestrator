"""Polygon integration steps (creating the problem, limits, statement, files, etc.).

Unlike `orchestrator/steps/` these aren't generative steps: the model isn't
called, a step is a sequence of Polygon API calls. Nothing from here is
imported at the package's top level — see the docstring of `orchestrator/polygon/__init__.py`.

Each step is a module with a `STEP: PolygonStep` constant (see `base.py`); the shared
"run if not run yet" logic is provided by `base.run_step`, a step
describes only its specifics.
"""
