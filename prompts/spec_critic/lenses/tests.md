Test excess. The tests plan step makes generators and tests from
these fields: every idea is a generator to write and tests to run on every
solution.

What to look for:
- a generator or a test idea that targets what another one already covers —
  propose merging;
- a test idea that no wrong approach of the spec, no limit and no special case
  of the setting needs;
- generator parameters or modes that no test uses;
- test groups where the limits don't need them, or more groups than the
  subtasks the limits define.

A test that kills a wrong approach of the spec, hits a limit or a special case
of the setting is needed: don't propose to remove it.
