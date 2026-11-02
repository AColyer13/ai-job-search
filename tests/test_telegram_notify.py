"""Telegram job notifications. No network: send is replaced by a list."""
import sys
import json
from pathlib import Path


def test_poll_preserves_authorized_reply_context_before_advancing_offset(tmp_path, monkeypatch):
    update = {'update_id': 7, 'message': {'chat': {'id': 42}, 'from': {'id': 42},
        'text': 'My answer', 'date': 123, 'reply_to_message': {'message_id': 5, 'text': 'The original question'}}}
    other = {'update_id': 8, 'message': {'chat': {'id': 99}, 'from': {'id': 99}, 'text': 'Unrelated private message'}}
    monkeypatch.setattr(telegram_notify, '_call', lambda *args, **kwargs: [update, other])
    offset = tmp_path / 'offset.json'
    telegram_notify.poll({'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_CHAT_ID': '42'}, offset_path=offset)
    archived = [json.loads(line) for line in offset.with_name('telegram_history.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(archived) == 1 and archived[0]['reply_context'] == 'The original question'
    assert json.loads(offset.read_text()) == 9

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
    assert telegram_notify.notify_new_jobs(
        store, "2026-10-28", env, send=lambda m, e: sent.append(m), company_check=lambda company: None,
    ) == 2
    assert len(sent) == 1
    assert telegram_notify.notify_new_jobs(store, "2026-10-28", env, send=lambda m, e: sent.append(m)) == 0
    assert len(sent) == 1


def test_a_long_poll_timeout_is_retried_as_a_short_poll(tmp_path, monkeypatch):
    calls = []

    def fake(token, method, payload=None, timeout=20):
        calls.append((payload or {}).get("timeout"))
        if (payload or {}).get("timeout"):
            raise telegram_notify.TelegramError("Telegram getUpdates failed: TimeoutError")
        return [{"update_id": 7, "message": {"text": "Yes", "chat": {"id": "1"}, "from": {"id": "1"}}}]

    monkeypatch.setattr(telegram_notify, "_call", fake)
    events = telegram_notify.poll({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}, wait=25,
                                   offset_path=tmp_path / "off.json")
    assert calls == [25, 0]
    assert events == [{"type": "text", "text": "Yes", "reply_to": None}]


def test_notify_does_nothing_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_notify, "SENT_PATH", tmp_path / "sent.json")
    sent = []
    assert telegram_notify.notify_new_jobs({"a": job(1)}, "2026-10-28", {}, send=lambda m, e: sent.append(m)) == 0
    assert sent == []


def test_notices_are_batched_into_one_summary_with_the_jobs(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    telegram_notify.queue_notice("Applied: A", path=outbox)
    telegram_notify.queue_notice("Could not finish: B", path=outbox)
    notices = telegram_notify.take_notices(path=outbox)
    assert notices == ["Applied: A", "Could not finish: B"]
    messages = telegram_notify.build_summary(notices, [], "2026-11-02")
    assert len(messages) == 1
    assert "Application updates (2)" in messages[0] and "Applied: A" in messages[0]
    telegram_notify.done_notices(path=outbox)
    assert telegram_notify.take_notices(path=outbox) == []


def test_failed_send_keeps_notices_for_next_summary(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    telegram_notify.queue_notice("one", path=outbox)
    assert telegram_notify.take_notices(path=outbox) == ["one"]  # taken, send "failed", done_notices not called
    telegram_notify.queue_notice("two", path=outbox)
    assert telegram_notify.take_notices(path=outbox) == ["one", "two"]
