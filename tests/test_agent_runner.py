"""Provider fallback for the headless agent runner. No network, no real CLIs."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import agent_runner  # noqa: E402


def fake_proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def failing(why):
    def run(*a, **k):
        raise agent_runner.ProviderFailed(why)
    return run


def test_first_working_provider_wins_and_later_ones_do_not_run():
    ran = []
    chain = [("claude", lambda *a, **k: ran.append("claude") or "done"),
             ("grok", lambda *a, **k: ran.append("grok") or "other")]
    result = agent_runner.run_agent("hi", providers=chain)
    assert (result.ok, result.provider, result.text, ran) == (True, "claude", "done", ["claude"])


def test_falls_back_and_records_why_the_first_failed():
    chain = [("claude", failing("usage limit")), ("grok", lambda *a, **k: "from grok")]
    result = agent_runner.run_agent("hi", providers=chain)
    assert result.ok and result.provider == "grok"
    assert result.attempts == [("claude", "usage limit")]


def test_empty_reply_counts_as_failure():
    chain = [("claude", lambda *a, **k: "  "), ("grok", lambda *a, **k: "x")]
    result = agent_runner.run_agent("hi", providers=chain)
    assert result.provider == "grok" and result.attempts == [("claude", "empty reply")]


def test_all_failing_returns_not_ok_without_raising():
    chain = [("claude", failing("a")), ("grok", failing("b"))]
    result = agent_runner.run_agent("hi", providers=chain)
    assert not result.ok and [n for n, _ in result.attempts] == ["claude", "grok"]


def test_claude_strips_api_key_so_it_stays_on_the_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-child")
    seen = {}

    def fake_run(cmd, cwd, env, timeout, stdin=None):
        seen.update(cmd=cmd, env=env, stdin=stdin)
        return fake_proc('{"result": "OK", "is_error": false}')

    monkeypatch.setattr(agent_runner, "_run", fake_run)
    assert agent_runner.run_claude("the prompt", ".", 5, 1) == "OK"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["stdin"] == "the prompt" and "-p" in seen["cmd"]


def test_claude_error_reply_raises(monkeypatch):
    monkeypatch.setattr(agent_runner, "_run", lambda *a, **k: fake_proc('{"result": "limit reached", "is_error": true}'))
    with pytest.raises(agent_runner.ProviderFailed, match="limit reached"):
        agent_runner.run_claude("p", ".", 5, 1)


def test_grok_402_balance_error_raises(monkeypatch):
    out = '{"type":"error","message":"API error (status 402 Payment Required): balance exhausted"}'
    monkeypatch.setattr(agent_runner, "_run", lambda *a, **k: fake_proc(out, returncode=1))
    with pytest.raises(agent_runner.ProviderFailed, match="402"):
        agent_runner.run_grok("p", ".", 5, 1, model="grok-4.7")


def test_grok_passes_model_and_returns_text(monkeypatch):
    seen = {}

    def fake_run(cmd, cwd, env, timeout, stdin=None):
        seen["cmd"] = cmd
        return fake_proc('{"text": "hello", "stopReason": "end_turn"}')

    monkeypatch.setattr(agent_runner, "_run", fake_run)
    assert agent_runner.run_grok("p", ".", 5, 1, model="grok-4.7") == "hello"
    assert seen["cmd"][seen["cmd"].index("-m") + 1] == "grok-4.7"


def test_antigravity_passes_the_prompt_as_an_argument_in_read_only_plan_mode(monkeypatch):
    seen = {}

    def fake_run(cmd, cwd, env, timeout, stdin=None):
        seen.update(cmd=cmd, stdin=stdin)
        return fake_proc('{"status": "SUCCESS", "response": "from agy", "num_turns": 1}')

    monkeypatch.setattr(agent_runner, "_run", fake_run)
    assert agent_runner.run_antigravity("the prompt", ".", 5, 1, model="some-model").strip() == "from agy"
    cmd = seen["cmd"]
    assert cmd[cmd.index("-p") + 1] == "the prompt" and seen["stdin"] is None  # agy ignores stdin
    assert cmd[cmd.index("--mode") + 1] == "plan" and "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--model") + 1] == "some-model"


def test_antigravity_failures_raise(monkeypatch):
    monkeypatch.setattr(agent_runner, "_run", lambda *a, **k: fake_proc('{"status": "ERROR", "error": "quota exceeded"}'))
    with pytest.raises(agent_runner.ProviderFailed, match="quota exceeded"):
        agent_runner.run_antigravity("p", ".", 5, 1)
    denied = '{"status": "SUCCESS", "response": "", "denied_actions": [{"action": "command"}]}'
    monkeypatch.setattr(agent_runner, "_run", lambda *a, **k: fake_proc(denied))
    with pytest.raises(agent_runner.ProviderFailed, match="cannot approve"):
        agent_runner.run_antigravity("p", ".", 5, 1)
    with pytest.raises(agent_runner.ProviderFailed, match="too long"):
        agent_runner.run_antigravity("x" * (agent_runner.MAX_ARG_CHARS + 1), ".", 5, 1)


def test_antigravity_is_third_in_the_default_chain_and_gemini_means_the_same(monkeypatch):
    monkeypatch.delenv("AGENT_PROVIDERS", raising=False)
    assert [n for n, _ in agent_runner.provider_chain({})] == ["claude", "grok", "antigravity"]
    assert [n for n, _ in agent_runner.provider_chain({"AGENT_PROVIDERS": "claude,gemini"})] == ["claude", "antigravity"]
    chain = [("claude", failing("limit")), ("grok", failing("402")), ("antigravity", lambda *a, **k: "from agy")]
    result = agent_runner.run_agent("hi", providers=chain)
    assert result.provider == "antigravity" and [n for n, _ in result.attempts] == ["claude", "grok"]


def test_provider_chain_defaults_and_local_is_opt_in(monkeypatch):
    monkeypatch.delenv("AGENT_PROVIDERS", raising=False)
    assert [n for n, _ in agent_runner.provider_chain({})] == ["claude", "grok", "antigravity"]
    assert [n for n, _ in agent_runner.provider_chain({"AGENT_PROVIDERS": "claude, grok, local"})] == ["claude", "grok", "local"]
