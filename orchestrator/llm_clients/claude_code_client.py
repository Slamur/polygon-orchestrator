"""An implementation of `orchestrator.llm_client.LLMClient` on top of the Claude Code CLI.

Each model call is a separate non-interactive `claude -p` process,
which authorizes the same way as interactive Claude Code (the
Pro/Max subscription from `~/.claude/`), not with `ANTHROPIC_API_KEY` — the usage comes from the
subscription limits, not the API balance. Everything CLI-specific (flags,
parsing its JSON output, recognizing an exhausted limit) lives only
here.

The process is isolated from the orchestrator's development environment:
- `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are stripped from the subprocess env —
  `load_dotenv()` puts the key from `.env` into the environment, and with it the CLI would silently go
  to API billing;
- running from an empty temporary directory + `--safe-mode` — CLAUDE.md, hooks,
  plugins, MCP servers and other user settings aren't picked up;
- `--tools ""` — the model has no tools (file reading, shell), only
  generating the response;
- `--no-session-persistence` — sessions aren't saved to `~/.claude/projects`.

The structured response — via `--json-schema` with the same
`RESPONSE_JSON_SCHEMA` as `AnthropicClient`'s; the CLI itself validates the model's response
against the schema and returns it in the `structured_output` field.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from orchestrator.llm_client import (
    LLMCallError,
    UsageLimitReachedError,
    build_system_prompt,
)
from orchestrator.model_contracts import (
    RESPONSE_JSON_SCHEMA,
    ModelResponse,
    model_response_from_payload,
)

logger = logging.getLogger(__name__)

# A nominal model class -> the model for `claude --model`. The same full IDs as
# `AnthropicClient`'s, not aliases (`opus`/`sonnet`): an alias over time
# starts pointing to a new model, and a model change would be a silent
# side effect of a CLI update rather than an explicit edit here.
MODEL_CLASS_TO_ID: dict[str, str] = {
    "strong-model": "claude-opus-5",
    "medium-model": "claude-sonnet-5",
    "fast-model": "claude-haiku-4-5-20251001",
}

# The path to the CLI binary; by default — `claude` from PATH.
CLAUDE_CODE_BIN_ENV = "CLAUDE_CODE_BIN"
_DEFAULT_CLAUDE_CODE_BIN = "claude"

# Variables with which the CLI authorizes via the API rather than the subscription.
_API_AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# A hard timeout for one call: a strong model with high effort on a large
# context answers in minutes, but not hours. Overridden in seconds by the
# `ORCHESTRATOR_LLM_TIMEOUT` variable.
TIMEOUT_ENV = "ORCHESTRATOR_LLM_TIMEOUT"
_DEFAULT_TIMEOUT_SECONDS = 30 * 60

# How the CLI reports an exhausted subscription limit ("Claude AI usage limit
# reached|<epoch>", "You've hit your limit · resets 5pm", etc.) — the exact
# text isn't documented and has changed between versions, so we recognize it by
# key phrases.
_USAGE_LIMIT_RE = re.compile(
    r"usage limit|hit your (?:usage )?limit|limit reached|weekly limit",
    re.IGNORECASE,
)

# An argument parsing error of an outdated CLI (e.g. without
# `--system-prompt-file`/`--safe-mode`).
_UNKNOWN_OPTION_RE = re.compile(r"unknown option", re.IGNORECASE)


class ClaudeCodeClient:
    """`LLMClient` on top of `claude -p` (see `orchestrator/llm_client.py`)."""

    def generate(
        self,
        *,
        model_class: str,
        effort: str,
        system_prompt: str,
        user_prompt: str,
        context_documents: dict[str, str] | None = None,
    ) -> ModelResponse:
        model_id = MODEL_CLASS_TO_ID[model_class]
        system = build_system_prompt(system_prompt, context_documents)

        with tempfile.TemporaryDirectory(prefix="orchestrator-claude-code-") as workdir:
            # The system prompt with documents — as a file, not an argument: on Linux
            # one argument is limited to 128 KB, and the steps' context is close to that.
            system_prompt_path = Path(workdir) / "system_prompt.md"
            system_prompt_path.write_text(system, encoding="utf-8")
            command = _build_command(model_id, effort, system_prompt_path)
            completed = _run(command, user_prompt, workdir, model_class)

        result = _parse_result(model_class, completed)
        _log_usage(model_class, model_id, result)
        return model_response_from_payload(result["structured_output"])


def _build_command(model_id: str, effort: str, system_prompt_path: Path) -> list[str]:
    return [
        os.environ.get(CLAUDE_CODE_BIN_ENV) or _DEFAULT_CLAUDE_CODE_BIN,
        "-p",
        "--output-format",
        "json",
        "--model",
        model_id,
        "--effort",
        effort,
        "--system-prompt-file",
        str(system_prompt_path),
        "--json-schema",
        json.dumps(RESPONSE_JSON_SCHEMA, ensure_ascii=False),
        "--tools",
        "",
        "--safe-mode",
        "--no-session-persistence",
    ]


def _subprocess_env() -> dict[str, str]:
    """The subprocess environment without the API authorization variables — otherwise the CLI
    would go to API billing instead of the subscription."""
    return {k: v for k, v in os.environ.items() if k not in _API_AUTH_ENV_VARS}


def _timeout_seconds() -> int:
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        seconds = int(raw)
    except ValueError:
        seconds = 0
    if seconds <= 0:
        raise LLMCallError(f"{TIMEOUT_ENV} must be a positive number of seconds, got {raw!r}")
    return seconds


def _run(
    command: list[str], user_prompt: str, workdir: str, model_class: str
) -> subprocess.CompletedProcess[str]:
    timeout = _timeout_seconds()
    try:
        return subprocess.run(
            command,
            input=user_prompt,
            capture_output=True,
            text=True,
            cwd=workdir,
            env=_subprocess_env(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise LLMCallError(
            f"ClaudeCodeClient: Claude Code CLI '{command[0]}' not found. "
            f"Install it or point {CLAUDE_CODE_BIN_ENV} at the binary."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LLMCallError(
            f"ClaudeCodeClient.generate(model_class={model_class!r}): "
            f"'claude -p' did not finish in {timeout}s, killed (the limit is set "
            f"in seconds by {TIMEOUT_ENV})"
        ) from exc


def _parse_result(model_class: str, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Parses the JSON output of `claude -p --output-format json` and raises
    a clear error on any unsuccessful outcome."""
    where = f"ClaudeCodeClient.generate(model_class={model_class!r})"
    stderr = (completed.stderr or "").strip()

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict):
        if _UNKNOWN_OPTION_RE.search(stderr):
            raise LLMCallError(
                f"{where}: the Claude Code CLI rejected an option — it is "
                f"probably too old, run `claude update` ({stderr})"
            )
        _raise_if_usage_limit(where, stderr)
        raise LLMCallError(
            f"{where}: 'claude -p' exited with code {completed.returncode} "
            f"without a JSON result (stderr: {stderr or '<empty>'})"
        )

    if result.get("is_error") or completed.returncode != 0:
        message = str(result.get("result") or stderr or "<no message>")
        _raise_if_usage_limit(where, message)
        raise LLMCallError(
            f"{where}: 'claude -p' failed (subtype={result.get('subtype')!r}, "
            f"exit code {completed.returncode}): {message}"
        )

    if not isinstance(result.get("structured_output"), dict):
        raise LLMCallError(
            f"{where}: the result has no structured_output (subtype={result.get('subtype')!r})"
        )
    return result


def _raise_if_usage_limit(where: str, message: str) -> None:
    if _USAGE_LIMIT_RE.search(message):
        raise UsageLimitReachedError(f"{where}: Claude usage limit reached: {message}")


def _log_usage(model_class: str, model_id: str, result: dict[str, Any]) -> None:
    """Logs usage. `total_cost_usd` when working on the subscription is an estimate in
    API prices, not an actual charge."""
    usage = result.get("usage") or {}
    logger.info(
        "ClaudeCodeClient.generate(model_class=%r): model=%s input_tokens=%s "
        "output_tokens=%s cache_creation_input_tokens=%s "
        "cache_read_input_tokens=%s num_turns=%s duration_ms=%s "
        "total_cost_usd(api-equivalent)=%s",
        model_class,
        model_id,
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_creation_input_tokens"),
        usage.get("cache_read_input_tokens"),
        result.get("num_turns"),
        result.get("duration_ms"),
        result.get("total_cost_usd"),
    )
