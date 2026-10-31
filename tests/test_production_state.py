"""Restart and concurrent-writer failures must preserve application history."""
from pathlib import Path
import subprocess
import sys

import pytest

SCRAPER = Path(__file__).resolve().parents[1] / "job_scraper"
sys.path.insert(0, str(SCRAPER))
import apply_agent
import tracking_audit
from state_store import StateError, load_state


@pytest.mark.parametrize("contents", ["{broken", "[]", "null", '{"j": 1}', '{"j": {}}'])
def test_damaged_state_is_preserved_and_never_becomes_an_empty_queue(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(StateError, match="preserve application history"):
        apply_agent.Agent(object(), state_path=path)
    assert path.read_text(encoding="utf-8") == contents


def test_missing_state_is_allowed_only_for_a_new_agent(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path, missing_ok=True) == {}
    with pytest.raises(StateError, match="audit incomplete"):
        load_state(path)


@pytest.mark.parametrize("contents", [None, "{broken", "[]"])
def test_audit_reports_unreadable_state(tmp_path, monkeypatch, contents):
    path = tmp_path / "state.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(tracking_audit, "STATE_PATH", path)
    problems = tracking_audit.audit(entries={}, tracker_rows=[])
    assert len(problems) == 1 and problems[0].startswith("STATE UNREADABLE:")


def test_queue_loads_state_after_acquiring_lock(tmp_path, monkeypatch):
    lock = tmp_path / "agent.lock"
    state = tmp_path / "state.json"
    monkeypatch.setattr(apply_agent, "LOCK_PATH", lock)
    monkeypatch.setattr(apply_agent, "STATE_PATH", state)
    monkeypatch.setattr(apply_agent, "Telegram", lambda: object())
    monkeypatch.setattr(apply_agent, "candidates", lambda day: [])
    original = apply_agent.Agent._load

    def checked_load(agent):
        assert lock.resolve() in apply_agent._HELD_LOCKS
        return original(agent)

    monkeypatch.setattr(apply_agent.Agent, "_load", checked_load)
    monkeypatch.setattr(apply_agent.Agent, "queue", lambda *a, **kw: [])
    assert apply_agent.main(["queue"]) == 0
    assert lock.resolve() not in apply_agent._HELD_LOCKS


def test_corrupt_state_returns_actionable_cli_error_and_releases_lock(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "agent.lock"
    state = tmp_path / "state.json"
    state.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(apply_agent, "LOCK_PATH", lock)
    monkeypatch.setattr(apply_agent, "STATE_PATH", state)
    monkeypatch.setattr(apply_agent, "Telegram", lambda: object())
    assert apply_agent.main(["queue"]) == 1
    assert "Restore a verified backup" in capsys.readouterr().out
    assert state.read_text(encoding="utf-8") == "{broken"
    assert lock.resolve() not in apply_agent._HELD_LOCKS


def test_other_process_cannot_take_lock_even_if_marker_is_removed(tmp_path):
    lock = tmp_path / "agent.lock"
    assert apply_agent.acquire_lock(lock)
    try:
        # Exercise OS exclusion, rather than merely trusting the PID marker.
        lock.unlink()
        code = "import apply_agent,sys; ok=apply_agent.acquire_lock(sys.argv[1]); print(ok); apply_agent.release_lock(sys.argv[1])"
        result = subprocess.run([sys.executable, "-c", code, str(lock)], cwd=SCRAPER,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"
    finally:
        apply_agent.release_lock(lock)
    assert apply_agent.acquire_lock(lock)
    apply_agent.release_lock(lock)


def test_process_crash_releases_os_lock(tmp_path):
    lock = tmp_path / "agent.lock"
    code = "import apply_agent,os,sys; assert apply_agent.acquire_lock(sys.argv[1]); os._exit(0)"
    subprocess.run([sys.executable, "-c", code, str(lock)], cwd=SCRAPER, check=True, timeout=15)
    assert apply_agent.acquire_lock(lock)
    apply_agent.release_lock(lock)


def test_dead_listener_does_not_receive_queue_requests(tmp_path, monkeypatch):
    import daily_digest
    monkeypatch.setattr(daily_digest, "DAILY", tmp_path)
    (tmp_path / "apply_agent.lock").write_text("listen 999999999", encoding="utf-8")
    assert daily_digest._listener_running() is False
