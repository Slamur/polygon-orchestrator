"""Routing generative steps by "model class" and `effort` (see
CLAUDE.md, the "Model routing by step criticality" section).

This module is deliberately vendor-independent: it knows only the nominal
model classes ("strong-model"/"medium-model"/"fast-model") and the `effort` levels
("low"/"medium"/"high"/"xhigh"/"max") per step, but not which concrete
vendor serves them. The actual call is made by an `LLMClient` object
(`orchestrator/llm_client.py`) — by default `ClaudeCodeClient`
(`orchestrator/llm_clients/claude_code_client.py`) or `AnthropicClient`
(`orchestrator/llm_clients/anthropic_client.py`) if
`ORCHESTRATOR_LLM_BACKEND=anthropic-api`, but it can be replaced
with `set_client(...)` by anything else (a local model, another
vendor), without touching this module, the prompts or the steps.
"""

from __future__ import annotations

from orchestrator.llm_client import LLMClient
from orchestrator.model_contracts import ModelResponse

# Re-export: some code and tests historically import `ModelResponse`
# from here (`from orchestrator.model_router import ModelResponse`).
__all__ = [
    "STEP_TO_MODEL",
    "STEP_TO_EFFORT",
    "ModelResponse",
    "set_client",
    "call_model",
]

# The mapping of a step to a nominal model class. Steps critical in content
# (constraints_pick, solutions_draft) — through the strong model.
# Test preparation is split into three steps, so that the substantive part goes
# separately from the voluminous one. tests_plan — test design, including
# adversarial tests for `solutions.known_wrong_approaches`: independent
# reasoning about how to break a particular wrong algorithm (see CLAUDE.md,
# "Model routing by step criticality"), but the response is small — a plan, not
# code; the medium model with high effort, the next escalation step — to the strong one.
# generators_draft and test_script_draft — implementing a ready plan: a lot of
# code, but little reasoning, so the medium model with medium effort — otherwise
# one call would reason longer than the timeout.
# statement_draft — the medium model: the task there is mostly about formatting
# by explicit rules from tutorials/statement.md, not about reasoning, so
# it doesn't need an Opus level (see CLAUDE.md).
# checker_draft — an optional step: formally mechanical when
# `checker.custom_comparison_notes` is complete and unambiguous (translating a text
# description into code following the read_and_check pattern), but a mistake here costs more than in
# an average mechanical step — a wrongly implemented comparison may silently
# accept wrong solutions or reject correct ones (see CLAUDE.md,
# "Model routing by step criticality"). The medium model is a compromise;
# if in practice `custom_comparison_notes` often turns out not precise
# enough and the step goes to uncertain too often, the next escalation step is
# to the strong model, like constraints_pick/solutions_draft.
# interactor_draft — an optional step (only interactive problems): unlike
# the checker it isn't translating one comparison rule into code, but
# implementing the whole protocol — reading queries in distrust mode, limits,
# possibly an adaptive strategy; a mistake silently breaks all of the problem's tests —
# so the strong model.
# spec_from_draft — not a pipeline step but `init --draft` (a spec from the author's
# draft, `orchestrator/spec_from_draft.py`): the spec is the input of all steps, and
# a mistake in it (or an unnoticed made-up value) spreads to all of them —
# so the strong model.
# tutorial_draft — a tutorial from the author's draft (`llm tutorial`): at the author's command
# the model writes proofs, and an unnoticed mistake in them reaches the contestants —
# so the strong model, even though the rest of the text expands the author's points.
STEP_TO_MODEL: dict[str, str] = {
    "statement_draft": "medium-model",
    "constraints_pick": "strong-model",
    "tests_plan": "medium-model",
    "generators_draft": "medium-model",
    "test_script_draft": "medium-model",
    "solutions_draft": "strong-model",
    "checker_draft": "medium-model",
    "interactor_draft": "strong-model",
    "spec_from_draft": "strong-model",
    "tutorial_draft": "strong-model",
}

# The effort level per step (shared by the vendors that support such a knob;
# those that don't are entitled to ignore it). constraints_pick/
# solutions_draft — the riskiest by the cost of a mistake; tests_plan — high
# because of designing adversarial tests, generators_draft/test_script_draft —
# medium, it's implementing the plan; checker_draft — high for the same
# reason as the model class: the cost of a mistake in comparing answers is high even
# with a formally mechanical implementation; statement_draft — medium, it's
# formatting, not reasoning; spec_from_draft and tutorial_draft — high, like
# the model class.
STEP_TO_EFFORT: dict[str, str] = {
    "statement_draft": "medium",
    "constraints_pick": "high",
    "tests_plan": "high",
    "generators_draft": "medium",
    "test_script_draft": "medium",
    "solutions_draft": "high",
    "checker_draft": "high",
    "interactor_draft": "high",
    "spec_from_draft": "high",
    "tutorial_draft": "high",
}

_client: LLMClient | None = None


def set_client(client: LLMClient) -> None:
    """Explicitly replaces the `LLMClient` that `call_model` uses.

    An extension point for other vendors (a local model, another API) or
    for test doubles — the calling code (e.g. `cli.py` or a test)
    decides which client to use, `model_router.py` itself isn't tied to a concrete
    vendor.
    """
    global _client
    _client = client


# The environment variable (or a line in `.env`) choosing the default
# client: "claude-code" (default) — the Claude Code CLI on the subscription,
# "anthropic-api" — the Anthropic API with ANTHROPIC_API_KEY.
LLM_BACKEND_ENV = "ORCHESTRATOR_LLM_BACKEND"
_DEFAULT_BACKEND = "claude-code"
_BACKENDS = ("claude-code", "anthropic-api")


def _default_client() -> LLMClient:
    # Lazy imports: if the client was explicitly replaced with `set_client`, the module
    # never touches `anthropic`/`python-dotenv` and doesn't require
    # ANTHROPIC_API_KEY — these are vendor packages, not a shared dependency of the
    # orchestrator.
    import os

    from dotenv import load_dotenv

    load_dotenv()
    backend = os.environ.get(LLM_BACKEND_ENV) or _DEFAULT_BACKEND
    if backend == "anthropic-api":
        from orchestrator.llm_clients.anthropic_client import AnthropicClient

        return AnthropicClient()
    if backend == "claude-code":
        from orchestrator.llm_clients.claude_code_client import ClaudeCodeClient

        return ClaudeCodeClient()
    raise ValueError(
        f"{LLM_BACKEND_ENV}={backend!r} is not a known LLM backend, allowed: {', '.join(_BACKENDS)}"
    )


def _get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = _default_client()
    return _client


def call_model(
    step: str,
    system_prompt: str,
    user_prompt: str,
    context_documents: dict[str, str] | None = None,
) -> ModelResponse:
    """Calls the model assigned to the step `step` via `STEP_TO_MODEL`/`STEP_TO_EFFORT`.

    `system_prompt` and `user_prompt` are already rendered prompt texts
    (`prompts/<step>/system.md` and `prompts/<step>/user.md.j2` after
    substituting the context); this function doesn't read or render them itself.
    `context_documents` — a "path" -> "contents" map of the attached documents
    (`orchestrator/steps/base.py:load_context_documents`), passed to the client
    as is.

    The model itself is called through the current `LLMClient` (see `set_client`) —
    how exactly it achieves a structured JSON response by the
    status/artifacts/notes contract (`docs/PROMPTS.md`) is decided by the client implementation,
    not this module.
    """
    client = _get_client()
    return client.generate(
        model_class=STEP_TO_MODEL[step],
        effort=STEP_TO_EFFORT[step],
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        context_documents=context_documents,
    )
