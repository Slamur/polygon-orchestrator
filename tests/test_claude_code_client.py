import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.llm_client import LLMCallError, UsageLimitReachedError
from orchestrator.llm_clients import claude_code_client
from orchestrator.llm_clients.claude_code_client import ClaudeCodeClient
from orchestrator.model_contracts import RESPONSE_JSON_SCHEMA, ModelResponse

_PAYLOAD = {
    "status": "proposed",
    "artifacts": [{"filename": "constraints.yaml", "content": "n: 100"}],
    "notes": [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}],
}


class _FakeRun:
    """Подменяет `subprocess.run`: запоминает вызов (и содержимое файла
    system-промпта, пока временный каталог ещё существует) и возвращает
    заданный результат — реальный `claude` не запускается."""

    def __init__(self, *, stdout="", stderr="", returncode=0, raises=None):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        system_prompt_path = Path(command[command.index("--system-prompt-file") + 1])
        self.calls.append(
            {
                "command": command,
                "system_prompt": system_prompt_path.read_text(encoding="utf-8"),
                **kwargs,
            }
        )
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def _success_stdout(payload=_PAYLOAD, **extra) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": payload,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "total_cost_usd": 0.01,
            **extra,
        }
    )


@pytest.fixture
def fake_run(monkeypatch):
    def install(**kwargs) -> _FakeRun:
        fake = _FakeRun(**kwargs)
        monkeypatch.setattr(claude_code_client.subprocess, "run", fake)
        return fake

    return install


def _generate(**overrides) -> ModelResponse:
    kwargs = {
        "model_class": "strong-model",
        "effort": "high",
        "system_prompt": "system text",
        "user_prompt": "user text",
    }
    kwargs.update(overrides)
    return ClaudeCodeClient().generate(**kwargs)


def _flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_generate_parses_structured_output(fake_run):
    fake_run(stdout=_success_stdout())

    result = _generate()

    assert isinstance(result, ModelResponse)
    assert result.status == "proposed"
    assert result.artifacts == {"constraints.yaml": "n: 100"}
    assert result.notes == [{"field": "n.max", "kind": "proposed", "explanation": "guessed"}]


def test_generate_passes_model_effort_schema_and_isolation_flags(fake_run):
    fake = fake_run(stdout=_success_stdout())

    _generate(model_class="medium-model", effort="xhigh")

    command = fake.calls[0]["command"]
    assert command[0] == "claude"
    assert "-p" in command
    assert _flag_value(command, "--output-format") == "json"
    assert _flag_value(command, "--model") == claude_code_client.MODEL_CLASS_TO_ID["medium-model"]
    assert _flag_value(command, "--effort") == "xhigh"
    assert json.loads(_flag_value(command, "--json-schema")) == RESPONSE_JSON_SCHEMA
    assert _flag_value(command, "--tools") == ""
    assert "--safe-mode" in command
    assert "--no-session-persistence" in command


def test_generate_sends_user_prompt_via_stdin_and_system_prompt_with_documents_via_file(fake_run):
    fake = fake_run(stdout=_success_stdout())

    _generate(
        system_prompt="system text",
        user_prompt="user text",
        context_documents={"tutorials/statement.md": "RULES"},
    )

    call = fake.calls[0]
    assert call["input"] == "user text"
    assert call["system_prompt"].startswith("system text")
    assert "--- Приложенный документ: tutorials/statement.md ---\nRULES" in call["system_prompt"]
    assert "user text" not in call["command"]


def test_generate_runs_in_a_temporary_directory_outside_the_repo(fake_run):
    fake = fake_run(stdout=_success_stdout())

    _generate()

    workdir = Path(fake.calls[0]["cwd"])
    assert workdir.name.startswith("orchestrator-claude-code-")
    assert Path.cwd() not in workdir.parents
    assert not workdir.exists()


def test_generate_strips_api_credentials_from_subprocess_env(fake_run, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    monkeypatch.setenv("SOME_OTHER_VAR", "kept")
    fake = fake_run(stdout=_success_stdout())

    _generate()

    env = fake.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["SOME_OTHER_VAR"] == "kept"


def test_generate_uses_binary_from_env(fake_run, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_BIN", "/opt/claude/bin/claude")
    fake = fake_run(stdout=_success_stdout())

    _generate()

    assert fake.calls[0]["command"][0] == "/opt/claude/bin/claude"


def test_generate_raises_usage_limit_error_on_limit_message(fake_run):
    fake_run(
        stdout=json.dumps(
            {"type": "result", "subtype": "success", "is_error": True,
             "result": "Claude AI usage limit reached|1760000000"}
        ),
        returncode=1,
    )

    with pytest.raises(UsageLimitReachedError, match="usage limit"):
        _generate()


def test_generate_raises_usage_limit_error_from_stderr_without_json(fake_run):
    fake_run(stdout="", stderr="You've hit your limit · resets 5pm", returncode=1)

    with pytest.raises(UsageLimitReachedError):
        _generate()


def test_generate_raises_llm_call_error_on_other_cli_errors(fake_run):
    fake_run(
        stdout=json.dumps(
            {"type": "result", "subtype": "error_during_execution", "is_error": True,
             "result": "API Error: 500"}
        ),
        returncode=1,
    )

    with pytest.raises(LLMCallError, match="error_during_execution") as excinfo:
        _generate()
    assert not isinstance(excinfo.value, UsageLimitReachedError)


def test_generate_hints_at_update_when_cli_rejects_an_option(fake_run):
    fake_run(stdout="", stderr="error: unknown option '--safe-mode'", returncode=1)

    with pytest.raises(LLMCallError, match="claude update"):
        _generate()


def test_generate_raises_if_structured_output_is_missing(fake_run):
    fake_run(stdout=_success_stdout(payload=None))

    with pytest.raises(LLMCallError, match="structured_output"):
        _generate()


def test_generate_raises_clear_error_if_cli_is_missing(fake_run):
    fake_run(raises=FileNotFoundError("claude"))

    with pytest.raises(LLMCallError, match="CLAUDE_CODE_BIN"):
        _generate()


def test_generate_raises_on_timeout(fake_run):
    fake_run(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1))

    with pytest.raises(LLMCallError, match="did not finish in 1800s"):
        _generate()


def test_generate_takes_timeout_from_env(fake_run, monkeypatch):
    monkeypatch.setenv(claude_code_client.TIMEOUT_ENV, "3600")
    fake = fake_run(stdout=_success_stdout())

    _generate()

    assert fake.calls[0]["timeout"] == 3600


@pytest.mark.parametrize("value", ["soon", "0", "-5"])
def test_generate_rejects_invalid_timeout_in_env(fake_run, monkeypatch, value):
    monkeypatch.setenv(claude_code_client.TIMEOUT_ENV, value)
    fake = fake_run()

    with pytest.raises(LLMCallError, match=claude_code_client.TIMEOUT_ENV):
        _generate()
    assert fake.calls == []


def test_model_classes_match_model_router():
    from orchestrator.model_router import STEP_TO_MODEL

    assert set(STEP_TO_MODEL.values()) <= set(claude_code_client.MODEL_CLASS_TO_ID)
