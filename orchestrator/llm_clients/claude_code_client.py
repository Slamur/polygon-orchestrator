"""Реализация `orchestrator.llm_client.LLMClient` поверх Claude Code CLI.

Каждый вызов модели — отдельный неинтерактивный процесс `claude -p`,
который авторизуется так же, как интерактивный Claude Code (подписка
Pro/Max из `~/.claude/`), а не по `ANTHROPIC_API_KEY` — расход идёт из
лимитов подписки, а не с API-баланса. Всё специфичное для CLI (флаги,
разбор его JSON-вывода, распознавание исчерпанного лимита) живёт только
здесь.

Процесс изолирован от окружения разработки оркестратора:
- `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` вырезаются из env подпроцесса —
  `load_dotenv()` кладёт ключ из `.env` в окружение, и с ним CLI молча ушёл
  бы на API-биллинг;
- запуск из пустого временного каталога + `--safe-mode` — не подхватываются
  CLAUDE.md, хуки, плагины, MCP-серверы и прочие пользовательские настройки;
- `--tools ""` — у модели нет инструментов (чтение файлов, shell), только
  генерация ответа;
- `--no-session-persistence` — сессии не сохраняются в `~/.claude/projects`.

Структурированный ответ — через `--json-schema` с той же
`RESPONSE_JSON_SCHEMA`, что и у `AnthropicClient`; CLI сам валидирует ответ
модели по схеме и возвращает его в поле `structured_output`.
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

from orchestrator.llm_client import LLMCallError, UsageLimitReachedError, build_system_prompt
from orchestrator.model_contracts import (
    RESPONSE_JSON_SCHEMA,
    ModelResponse,
    model_response_from_payload,
)

logger = logging.getLogger(__name__)

# Условный класс модели -> модель для `claude --model`. Те же полные ID, что
# и у `AnthropicClient`, а не алиасы (`opus`/`sonnet`): алиас со временем
# начинает указывать на новую модель, и смена модели была бы молчаливым
# побочным эффектом обновления CLI, а не явной правкой здесь.
MODEL_CLASS_TO_ID: dict[str, str] = {
    "strong-model": "claude-opus-5",
    "medium-model": "claude-sonnet-5",
    "fast-model": "claude-haiku-4-5-20251001",
}

# Путь к бинарю CLI; по умолчанию — `claude` из PATH.
CLAUDE_CODE_BIN_ENV = "CLAUDE_CODE_BIN"
_DEFAULT_CLAUDE_CODE_BIN = "claude"

# Переменные, с которыми CLI авторизуется по API, а не подпиской.
_API_AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Жёсткий таймаут на один вызов: сильная модель с высоким effort на большом
# контексте отвечает минутами, но не часами. Переопределяется в секундах
# переменной `ORCHESTRATOR_LLM_TIMEOUT`.
TIMEOUT_ENV = "ORCHESTRATOR_LLM_TIMEOUT"
_DEFAULT_TIMEOUT_SECONDS = 30 * 60

# Как CLI сообщает об исчерпанном лимите подписки ("Claude AI usage limit
# reached|<epoch>", "You've hit your limit · resets 5pm" и т.п.) — точный
# текст не документирован и менялся между версиями, поэтому распознаём по
# ключевым фразам.
_USAGE_LIMIT_RE = re.compile(
    r"usage limit|hit your (?:usage )?limit|limit reached|weekly limit",
    re.IGNORECASE,
)

# Ошибка разбора аргументов у устаревшего CLI (например, без
# `--system-prompt-file`/`--safe-mode`).
_UNKNOWN_OPTION_RE = re.compile(r"unknown option", re.IGNORECASE)


class ClaudeCodeClient:
    """`LLMClient` поверх `claude -p` (см. `orchestrator/llm_client.py`)."""

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
            # System-промпт с документами — файлом, а не аргументом: в Linux
            # один аргумент ограничен 128 КБ, а контекст шагов к этому близок.
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
        "--output-format", "json",
        "--model", model_id,
        "--effort", effort,
        "--system-prompt-file", str(system_prompt_path),
        "--json-schema", json.dumps(RESPONSE_JSON_SCHEMA, ensure_ascii=False),
        "--tools", "",
        "--safe-mode",
        "--no-session-persistence",
    ]


def _subprocess_env() -> dict[str, str]:
    """Окружение подпроцесса без переменных API-авторизации — иначе CLI
    пошёл бы на API-биллинг вместо подписки."""
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


def _parse_result(
    model_class: str, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Разбирает JSON-вывод `claude -p --output-format json` и поднимает
    понятную ошибку на любом неуспешном исходе."""
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
            f"{where}: the result has no structured_output "
            f"(subtype={result.get('subtype')!r})"
        )
    return result


def _raise_if_usage_limit(where: str, message: str) -> None:
    if _USAGE_LIMIT_RE.search(message):
        raise UsageLimitReachedError(f"{where}: Claude usage limit reached: {message}")


def _log_usage(model_class: str, model_id: str, result: dict[str, Any]) -> None:
    """Логирует usage. `total_cost_usd` при работе по подписке — оценка в
    ценах API, а не реальное списание."""
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
