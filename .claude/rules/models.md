---
paths:
  - "orchestrator/model_router.py"
  - "orchestrator/model_contracts.py"
  - "orchestrator/llm_client.py"
  - "orchestrator/llm_clients/**"
  - "tests/test_model_router.py"
  - "tests/test_anthropic_client.py"
  - "tests/test_claude_code_client.py"
---

# Models: classes, effort, clients

The shared mechanics of the steps (revision, cache, escalation) — in `.claude/rules/steps.md`.

## Model routing by step criticality

Steps that are critical in content (where a mistake costs more — wrong
asymptotics, wrong limits, an incorrect solution) go through a
stronger model; mechanical/boilerplate steps — through a faster/cheaper one.

| Step                        | Criticality | Proposed model class |
|-----------------------------|-------------|----------------------------|
| `constraints_pick`          | high        | strong model             |
| `solutions_draft`           | high        | strong model             |
| `checker_draft`              | high (with incomplete `custom_comparison_notes`) | medium model |
| `statement_draft` (LaTeX)   | medium      | medium model              |
| `tests_plan`                | medium — designing the base tests | medium model, high effort |
| `generators_draft`          | low — implementing the plan | medium model, medium effort |
| `test_script_draft`         | low — implementing the plan | medium model, medium effort |
| `interactor_draft`          | high — the whole protocol, a mistake breaks all tests | strong model |
| `spec_from_draft` (`init --draft`) | high — the spec is the input of all steps | strong model |
| `tutorial_draft` (`llm tutorial`) | high — proofs at the author's request | strong model |
| `spec_critic` (`init --draft`, `llm critic`; every lens) | medium — every finding goes through the generator or the author | medium model, high effort |

`checker_draft` is formally mechanical when `custom_comparison_notes`
is complete and unambiguous (translating a text description into code following the
read_and_check pattern) — but a mistake here costs more than in an average mechanical step:
a wrongly implemented comparison may silently accept wrong solutions or
reject correct ones, and this may go unnoticed for a long time. The medium model is
a compromise: if in practice `custom_comparison_notes` often turns out to be
not precise enough and the step goes to `uncertain` too often
(which by itself isn't bad — that's by design), the step can be raised to the strong
model, like `constraints_pick`/`solutions_draft`.

Test preparation is split into `tests_plan` and `generators_draft`/`test_script_draft`
for a reason: implementing a generator from a ready idea is mechanical, while
**coming up with** the tests — which cases of the input the plan must cover — is
substantive; as one call with
high effort and dozens of files in the response it didn't fit into the timeout.
So the reasoning is in `tests_plan` (medium model, high effort, a small
response), the code — in `generators_draft`/`test_script_draft` (medium model,
medium effort). If the quality of the proposed tests turns out weak, the next
escalation step is `tests_plan` to the strong model, like
`constraints_pick`/`solutions_draft`.

Model classes ("strong-model"/"medium-model"/"fast-model") and the `effort` per
step live in `orchestrator/model_router.py` — this module is vendor-independent,
it doesn't know concrete model names. Mapping a class to a vendor's concrete model
(e.g. `"strong-model" -> "claude-opus-5"`), authorization, retry
and the structured output format — all of this is encapsulated in the
`LLMClient` implementation (`orchestrator/llm_client.py`), by default `ClaudeCodeClient`
(`orchestrator/llm_clients/claude_code_client.py`: models through the Claude Code
CLI, spending the subscription limits rather than the API balance — see
`docs/SETUP.md`); with `ORCHESTRATOR_LLM_BACKEND=anthropic-api` —
`AnthropicClient` (`orchestrator/llm_clients/anthropic_client.py`, the same
models through the Anthropic API). Prompts must not
depend on which model/vendor exactly runs them — none of this
information may leak into `prompts/`.
