# Default resources

External files a problem's build uses, as Polygon's "Resource files" of a problem:
`testlib.h` and the files for building the statement PDF. One directory per
upstream source, each with a README: where the files come from, the pinned version, how
to update them.

- The files are copied from upstream without changes and are not edited by hand.
- They are the defaults. A problem that needs its own version of a resource keeps it in
  `problems/<problem path>/resources/`, next to its `problem_lib.h`: a build looks there
  first (`orchestrator/resources.py`).
- The author's own files the problems start from (`problem_lib.h`, solution skeletons)
  are templates, they live in `templates/`.
