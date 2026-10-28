"""Telegram job notifications. No network: send is replaced by a list."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import telegram_notify  # noqa: E402


def job(n, fit="high", score=80, **extra):
    row = {
        "found_on": "2026-10-28", "fit": fit, "fit_score": score, "title": f"Engineer {n}",
        "company": f"Co{n}", "location": "Remote", "url": f"https://example.com/jobs/{n}",
        "description": "x" * 400, "body_status": "opened", "verified": True, "disqualified": False,
    }
    row.update(extra)
    return row


def test_read_env_file_wins_and_environment_fills_gaps(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nTELEGRAM_BOT_TOKEN=abc\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "'42'")
    values = telegram_notify.read_env(env_file)
    assert values == {"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_CHAT_ID": "'42'"}


def test_read_env_strips_quotes_in_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID='42'\n", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram_notify.read_env(env_file) == {"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_CHAT_ID": "42"}


def test_save_env_value_replaces_existing_line(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    telegram_notify.save_env_value("TELEGRAM_CHAT_ID", "99", env_file)
    assert env_file.read_text(encoding="utf-8").splitlines() == ["TELEGRAM_BOT_TOKEN=abc", "TELEGRAM_CHAT_ID=99"]


def test_pending_skips_sent_applied_and_other_days_best_first():
    store = {
        "a": job(1, fit="medium", score=50),
        "b": job(2, fit="high", score=90),
        "c": job(3),
        "d": job(4),
        "e": job(5, found_on="2026-10-27"),
    }
    out = telegram_notify.pending_jobs(
        store, "2026-10-28", sent={"https://example.com/jobs/3"}, applied_urls=["https://example.com/jobs/4"],
    )
    assert [j["title"] for j in out] == ["Engineer 2", "Engineer 1"]


def test_messages_stay_under_telegram_limit_and_escape_html():
    jobs = [job(i, title=f"<i>Dev {i}</i> & more " + "y" * 400) for i in range(12)]
    messages = telegram_notify.build_messages(jobs, "2026-10-28")
    assert len(messages) > 1
    assert all(len(m) <= telegram_notify.MESSAGE_LIMIT for m in messages)
    assert "<i>" not in messages[0]
    assert "&lt;i&gt;Dev 0" in messages[0]
    assert "&amp; more" in messages[0]


def test_notify_sends_once_then_remembers(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_notify, "SENT_PATH", tmp_path / "sent.json")
    import applied_log
    monkeypatch.setattr(applied_log, "load_log", lambda path=None: {})
    store = {"a": job(1), "b": job(2)}
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
    sent = []
    assert telegram_notify.notify_new_jobs(store, "2026-10-28", env, send=lambda m, e: sent.append(m)) == 2
    assert len(sent) == 1
    assert telegram_notify.notify_new_jobs(store, "2026-10-28", env, send=lambda m, e: sent.append(m)) == 0
    assert len(sent) == 1


def test_notify_does_nothing_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_notify, "SENT_PATH", tmp_path / "sent.json")
    sent = []
    assert telegram_notify.notify_new_jobs({"a": job(1)}, "2026-10-28", {}, send=lambda m, e: sent.append(m)) == 0
    assert sent == []
