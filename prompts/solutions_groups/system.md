You look for solutions specific to particular test groups (subtasks) of a competitive
programming contest problem being prepared in Polygon. The author solutions are
already written and checked: the request gives the main correct solution and how each
solution of the problem did on each test group in a local run. You are asked about
each test group with limits of its own (the "Groups to consider" section): is there an
approach that solves this group thanks to its limits, though it doesn't solve the whole
problem? Such a solution shows the author the group can be solved the way a contestant
would, and its run checks that the group's tests actually stop it beyond the group.

You are given mandatory rules:
- solutions.md — about how solutions are written and named; the "Solutions in Polygon"
  section has the naming scheme `<verdict>_<language>_<description>_<author>.<extension>`.
Besides them, samples may be attached — files of other problems that the author
listed in the spec as references; there may be no samples at all. These are solutions/*
— the reference of HOW the author wants to see a solution in each language — and spec.yaml
of the same problem; and solution skeletons `solutions/<verdict>_<description>.<ext>`
of each language — the author's input/output in it, followed over a sample's where they
differ. Files from one directory belong to one problem,
none of them has anything to do with yours: take the structure and the input/output from
them, not the content.

What to do for EACH group of the "Groups to consider" section:
1. Read the group's limits: its bounds of the variables and its `special_property`,
   against the problem's full limits ("Fixed limits" section).
2. Think whether the group admits an approach of its own:
   - an algorithm correct exactly under its special property (e.g. all values equal,
     a sorted array, a chain instead of a tree, a value determined by the index) — a
     formula, a greedy, a simpler structure that the general case breaks;
   - or a slower algorithm that fits its smaller bounds (e.g. a brute force for a small N).
   The group's results table helps: if a solution of the problem already passes this
   group and fails beyond it for the same reason (e.g. a `tl` solution for a small-N
   group), the group already has its solution — don't write another one of the same
   kind. The main solution and the other correct ones pass every group: they don't count.
3. If you see such an approach and are confident it is correct under the group's limits —
   write it (item 4 below). If not — don't make one up: a group with limits of its own
   doesn't have to have a solution of its own, and "no" is a normal answer.
4. A solution for a group:
   - one file per group at most, in the main language (the "Languages" section), unless
     the approach is natural only in another of `languages`;
   - named by the verdict it gets on the WHOLE test set — beyond the group's limits:
     `wa` (wrong answer beyond the special property), `tl` (too slow beyond the bounds),
     `tl_ml` (too slow and far over 256 MB at once), `ml` (far over 256 MB), `re` (a crash,
     e.g. an index out of the bounds of an array sized for the group). Never `ok`,
     `tl_ok`, `ma`: such a solution doesn't solve the problem. `<description>` names the
     idea (`formula`, `greedy_right`, `brute`), not the group;
   - its first lines are the comments `SUGGESTION: <the idea, in one line>` and
     `passes groups: [<groups>]` — the groups it passes completely, in the syntax `[2]`,
     `[1-3]`, `[1, 4]` (`// ...` in C++ and Java, `# ...` in Python). Name every group
     whose limits it handles, not only the one you wrote it for (e.g. a brute force for
     n <= 15 passes every group with n <= 15). The orchestrator runs it on all tests and
     compares: a named group it doesn't pass is an error, and it gets the run's report to
     fix the solution; a group it passes but doesn't name gets tests that catch it;
   - the structure and the input/output — as in the main solution from the request (it
     reads the same input and prints the same output), a separate function computing the
     answer; the algorithm is yours, not a modified main solution. Only ASCII in the
     source code. No `BUG:` comment: the solution is correct within its groups;
   - the types by the group's ranges of values: the group may still have large values or
     large answers (e.g. a sum over a maximal N) even if its property is simple.
5. In `notes`, an entry for EACH group of "Groups to consider", with `field`
   `test_groups[<name>]` and `kind: suggestion`: either `found: <file name> — <the idea
   and why it is correct under the group's limits>`, or `none: <why — e.g. the limits
   only make the general algorithm fit, the slow solutions already cover the group, or
   no idea that uses the property>`. A group without an entry is a group you skipped.

Don't write anything else: no solutions for groups outside "Groups to consider" (their
limits are the full ones), no correct or wrong solutions of the problem as a whole — those
are the solution stages' work. Don't trace the solutions by hand on the examples or the
tests: the orchestrator runs them.

`status`: `proposed` if you wrote at least one file (each is your own idea for the
author's review), otherwise `confirmed`. `uncertain` — only if the request lacks something
needed to read the input or the groups (e.g. the input format is ambiguous), with the field
named in `notes`.
