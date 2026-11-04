"""The LLM vendor interface behind which `orchestrator/model_router.py` calls
the model — the only point that needs implementing to plug in
another vendor (e.g. a local model instead of the Anthropic API) or
to replace the client in tests.

`model_router.py` knows only about the nominal model classes
("strong-model"/"medium-model"/"fast-model") and `effort`
("low"/"medium"/"high"/"xhigh"/"max") — which vendor exactly, what its
structured output format is (tool use, JSON mode, text parsing) and how
it distinguishes model classes and interprets `effort` (maps them to its models/
parameters or ignores them, if that doesn't suit the vendor) — is entirely encapsulated
in the `LLMClient` implementation. An example implementation for the Anthropic API —
`orchestrator/llm_clients/anthropic_client.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orchestrator.model_contracts import ModelResponse


class LLMCallError(RuntimeError):
    """The model call gave no response: a timeout, a CLI or API error, a response without
    structured output, etc.

    This isn't a model response that failed the check (that one is saved and
    applied by `llm apply`) — there's nothing to save, the next run of the step
    calls the model again. The pipeline turns it into a step error, not a
    traceback.
    """


class UsageLimitReachedError(LLMCallError):
    """The vendor refused because a usage limit was exhausted (e.g. the
    5-hour or weekly subscription limit), not because of a request error.

    Unlike other client errors, this isn't a problem of a particular problem:
    the following calls until the limit resets will fail the same way, and repeating the request
    is pointless.
    """


@runtime_checkable
class LLMClient(Protocol):
    """Calls one generative model and returns the parsed response.

    The implementation itself is responsible for making the model return valid
    JSON by the status/artifacts/notes contract (`docs/PROMPTS.md`) — e.g.
    through forced tool use, structured outputs or text parsing —
    and for its own details like authorization, retry and usage logging.
    """

    def generate(
        self,
        *,
        model_class: str,
        effort: str,
        system_prompt: str,
        user_prompt: str,
        context_documents: dict[str, str] | None = None,
    ) -> ModelResponse:
        """Calls the model `model_class` with the given `effort` on a pair of prompts.

        `model_class` — one of the keys of `STEP_TO_MODEL` in `model_router.py`
        ("strong-model"/"medium-model"/"fast-model"); how it resolves to a
        concrete vendor model is decided by the implementation itself.
        `effort` — one of `STEP_TO_EFFORT` in `model_router.py`; vendors without
        a notion of effort are free to ignore it.
        `context_documents` — a "document path" -> "contents" map
        (`tutorials/*.md`, `problem_lib.h`, `generators/*.cpp`, etc.,
        see `orchestrator/steps/base.py:STEP_CONTEXT_DOCUMENTS`), which the step
        must attach as mandatory rules/samples — not a retelling from
        memory. `None`/an empty map — behave as before (without documents).
        """
        ...


def build_system_prompt(system_prompt: str, context_documents: dict[str, str] | None) -> str:
    """Joins `system_prompt` with the attached context documents.

    Shared by all clients whose context documents travel in the
    system prompt. The documents go after `system_prompt` in the order of
    `context_documents` (see
    `orchestrator/steps/base.py:load_context_documents` — the fixed
    STEP_CONTEXT_DOCUMENTS list, then extra_paths), each with its path
    as a header, so that the model can refer to it. `None`/an empty map
    — `system` stays just `system_prompt` (backward compatibility).
    """
    if not context_documents:
        return system_prompt

    parts = [system_prompt]
    for path, content in context_documents.items():
        parts.append(f"--- Приложенный документ: {path} ---\n{content}")
    return "\n\n".join(parts)
