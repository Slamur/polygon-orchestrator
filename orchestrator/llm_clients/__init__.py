"""Concrete implementations of `orchestrator.llm_client.LLMClient` by vendor.

Nothing from here is imported at the package's top level
(`orchestrator/model_router.py` pulls in `AnthropicClient` lazily, only
if the client wasn't explicitly replaced with `set_client`) — so a module that
uses only another vendor doesn't pull in the Anthropic SDK dependencies.
"""
