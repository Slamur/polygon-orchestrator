Extra limits. The limits step turns this section into the
constraints of the statement and the validator: every variable, guarantee and
limit is a check in the validator and a line in the statement.

What to look for:
- variables nothing depends on: a limit no algorithm and no test needs;
- `special_guarantees` that follow from the other limits or from the format,
  or restate a variable's min/max;
- a `complexity_reasoning` that retells the algorithm instead of saying why
  these limits separate the intended asymptotics from the slower ones;
- `uncertain_points` the spec already answers.

The limits themselves and the asymptotics are set by the author: you may ask
(`question`) whether a limit is needed, but don't propose other numbers.
