"""Send new high and medium fit jobs to Adam on Telegram.

    python job_scraper/telegram_notify.py setup     find your chat id (message the bot first) and save it to .env
    python job_scraper/telegram_notify.py setup --wait 180   listen for up to 180 seconds for your message
    python job_scraper/telegram_notify.py test      send a test message
    python job_scraper/telegram_notify.py digest    send today's new fit jobs that were not sent before

The searches send nothing. The Windows task "Adam job alerts" runs `run_telegram_digest.cmd` (this file's
`digest`) at DIGEST_TIMES only, Adam's choice on 2026-11-08, so the new jobs and the queued application updates
arrive as one summary at those four times and never in between.

The bot token and chat id live in .env at the repo root (gitignored). Environment variables of the
same name only fill in what the file leaves out. The bot only ever writes to the one saved chat id. It reads nothing but
the setup call. Jobs already sent are remembered in daily/telegram_sent.json, and jobs already in the
applied log are skipped.
"""
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import company_standing  # noqa: E402
import daily_digest  # noqa: E402

ENV_PATH = daily_digest.ROOT / ".env"
SENT_PATH = daily_digest.DAILY / "telegram_sent.json"
API = "https://api.telegram.org/bot{token}/{method}"
MESSAGE_LIMIT = 4000  # Telegram's cap is 4096 characters
MAX_JOBS_PER_RUN = 15
DIGEST_TIMES = ("08:30", "11:30", "14:30", "16:35")  # local time; the scheduled task uses these
TIMEOUT = 20


class TelegramError(Exception):
    pass


def read_env(path=None):
    """Settings from .env. Environment variables only fill in what .env leaves out, because a
    machine-wide TELEGRAM_BOT_TOKEN for some other bot must not override this project's file."""
    values = {}
    path = Path(path or ENV_PATH)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not values.get(key) and os.environ.get(key):
            values[key] = os.environ[key]
    return values


def save_env_value(key, value, path=None):
    path = Path(path or ENV_PATH)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.startswith(key + "=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _call(token, method, payload=None, timeout=TIMEOUT):
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(payload or {}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TelegramError(f"Telegram {method} failed: HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TelegramError(f"Telegram {method} failed: {type(e).__name__}") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram {method} failed: {body.get('description', 'unknown error')}")
    return body["result"]


def send_message(text, env=None, buttons=None):
    """Send one message. `buttons` is a list of rows of (label, callback_data) shown under it."""
    env = read_env() if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise TelegramError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set (run: telegram_notify.py setup)")
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in buttons]})
    return _call(token, "sendMessage", payload)


OFFSET_PATH = daily_digest.DAILY / "telegram_offset.json"


def _load_offset(path=None):
    try:
        return int(json.loads(Path(path or OFFSET_PATH).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return 0


def _save_offset(value, path=None):
    path = Path(path or OFFSET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def parse_updates(updates, chat_id):
    """Button taps and text replies from `chat_id` only. Anything else is dropped: the bot obeys one person."""
    events = []
    for u in updates:
        cb = u.get("callback_query")
        msg = u.get("message")
        if cb and str((cb.get("message") or {}).get("chat", {}).get("id")) == str(chat_id) and str((cb.get("from") or {}).get("id")) == str(chat_id):
            events.append({"type": "button", "data": cb.get("data", ""), "callback_id": cb.get("id"),
                           "message_id": (cb.get("message") or {}).get("message_id")})
        elif msg and str((msg.get("chat") or {}).get("id")) == str(chat_id) and str((msg.get("from") or {}).get("id")) == str(chat_id) and msg.get("text"):
            events.append({"type": "text", "text": msg["text"],
                           "reply_to": (msg.get("reply_to_message") or {}).get("message_id")})
    return events


def poll(env=None, wait=0, offset_path=None):
    """New button taps and replies since the last call. Marks them read, so each arrives once."""
    env = read_env() if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise TelegramError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set")
    offset = _load_offset(offset_path)
    payload = {"offset": offset, "timeout": wait, "allowed_updates": json.dumps(["message", "callback_query"])}
    try:
        updates = _call(token, "getUpdates", payload, timeout=TIMEOUT + wait)
    except TelegramError as e:
        # A long poll that dies at the socket timeout can drop the reply Adam just sent. One short poll picks it up.
        if not wait or "TimeoutError" not in str(e):
            raise
        updates = _call(token, "getUpdates", {**payload, "timeout": 0}, timeout=TIMEOUT)
    if updates:
        # Preserve only Adam's accepted events before advancing the offset. Never
        # turn arbitrary chat prose into an application answer automatically.
        archive = Path(offset_path or OFFSET_PATH).with_name("telegram_history.jsonl")
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open("a", encoding="utf-8") as file:
                for update in updates:
                    accepted = parse_updates([update], chat)
                    if accepted:
                        message = update.get("message") or {}
                        file.write(json.dumps({"update_id": update["update_id"], "events": accepted,
                            "date": message.get("date"), "reply_context":
                            (message.get("reply_to_message") or {}).get("text")}, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise TelegramError("Could not preserve Telegram history; offset was not advanced") from exc
        _save_offset(max(u["update_id"] for u in updates) + 1, offset_path)
    return parse_updates(updates, chat)


def answer_button(callback_id, text="Got it", env=None):
    """Stop the spinner on a tapped button."""
    env = read_env() if env is None else env
    try:
        _call(env["TELEGRAM_BOT_TOKEN"], "answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except (TelegramError, KeyError):
        pass


def find_chat_id(env=None, wait=0):
    """The chat id of the most recent private message sent to the bot, or None.

    With `wait` seconds, keeps listening (long polling) so a message sent while this runs is caught.
    """
    env = read_env() if env is None else env
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set in .env")
    deadline = time.monotonic() + wait
    while True:
        poll = 25 if wait else 0
        updates = _call(token, "getUpdates", {"timeout": poll, "allowed_updates": json.dumps(["message"])},
                        timeout=TIMEOUT + poll)
        for update in reversed(updates):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private":
                return str(chat["id"])
        if time.monotonic() >= deadline:
            return None


def _rank(job):
    return (daily_digest.ORDER.get(job.get("fit"), 2), -(job.get("fit_score") or 0))


def load_sent(path=None):
    path = Path(path or SENT_PATH)
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_sent(urls, path=None):
    path = Path(path or SENT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(urls)), encoding="utf-8")


OUTBOX_PATH = daily_digest.DAILY / "telegram_outbox.jsonl"


def queue_notice(text, path=None):
    """Hold an automatic notice for the next job summary (DIGEST_TIMES) instead of sending it now."""
    path = Path(path or OUTBOX_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "text": text}, ensure_ascii=False) + "\n")


def take_notices(path=None):
    """Everything waiting in the outbox, oldest first. The file is moved aside first so a notice queued
    meanwhile is kept for the next summary."""
    path = Path(path or OUTBOX_PATH)
    if not path.exists():
        return []
    taken = path.with_suffix(".sending")
    try:
        if taken.exists():  # a send that failed earlier: keep its notices first
            with taken.open("a", encoding="utf-8") as out:
                out.write(path.read_text(encoding="utf-8"))
            path.unlink()
        else:
            path.replace(taken)
    except OSError:
        return []
    notices = []
    for line in taken.read_text(encoding="utf-8").splitlines():
        try:
            notices.append(json.loads(line)["text"])
        except (ValueError, KeyError, TypeError):
            continue
    return notices


def done_notices(path=None):
    taken = Path(path or OUTBOX_PATH).with_suffix(".sending")
    try:
        taken.unlink()
    except OSError:
        pass


def build_summary(notices, jobs, day):
    """One summary: what the agent did since the last one, then the new jobs. Split only at Telegram's length cap."""
    blocks = []
    if notices:
        blocks.append(f"<b>Application updates ({len(notices)})</b>")
        blocks.extend(notices)
    shown = jobs[:MAX_JOBS_PER_RUN]
    if jobs:
        head = f"<b>{len(jobs)} new job{'s' if len(jobs) != 1 else ''} for {day}</b>"
        if len(jobs) > len(shown):
            head += f" (top {len(shown)} shown, the rest are in daily/latest.docx)"
        blocks.append(head)
        blocks.extend(format_job(j) for j in shown)
    messages, current = [], ""
    for block in blocks:
        block = block[:MESSAGE_LIMIT]
        if current and len(current) + len(block) + 2 > MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        messages.append(current)
    return messages


def pending_jobs(store, day, sent, applied_urls=(), back=0):
    """Fit jobs found on `day` (or up to `back` days before it) that were not sent before and are not in the
    applied log, best first."""
    skip = {daily_digest._key(u) for u in applied_urls} | {daily_digest._key(u) for u in sent}
    first = date.fromisoformat(day)
    found = []
    for n in range(back + 1):
        found += daily_digest.jobs_found_on(store, (first - timedelta(days=n)).isoformat())
    jobs = daily_digest.fit_jobs(found)
    jobs = [j for j in jobs if daily_digest._key(j.get("url")) not in skip]
    return sorted(jobs, key=_rank)


def handling_label(link):
    """Who applies: the agent fills Greenhouse, Lever and Ashby forms (asking on Telegram first); anything else is yours."""
    import apply_forms  # imported here so telegram_notify stays importable on its own
    if link and apply_forms.detect_ats(link):
        return "AI handles this one here: I will ask before anything is sent"
    return "You apply by hand (not a form the AI can fill)"


def format_job(job):
    esc = html.escape
    title = esc(job.get("title") or "Untitled")
    company = esc(job.get("company") or "Company not listed")
    fit = (job.get("fit") or "").upper()
    score = job.get("fit_score")
    head = f"{company_standing.mark_line(job.get('company'))}\n<b>{title}</b> at {company}"
    meta = f"{fit} fit" + (f" ({score})" if score is not None else "") + f" | {esc(daily_digest._where(job))}"
    link = daily_digest.apply_link(job)
    lines = [head, meta, handling_label(link)]
    if link:
        lines.append(f'<a href="{esc(link, quote=True)}">Open posting</a>')
    return "\n".join(lines)


def build_messages(jobs, day):
    """Chunk the job blocks into messages under Telegram's length cap."""
    shown = jobs[:MAX_JOBS_PER_RUN]
    header = f"{len(jobs)} new job{'s' if len(jobs) != 1 else ''} for {day}"
    if len(jobs) > len(shown):
        header += f" (top {len(shown)} shown, the rest are in daily/latest.docx)"
    messages, current = [], header
    for job in shown:
        block = format_job(job)
        if len(current) + len(block) + 2 > MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def notify_new_jobs(store, day=None, env=None, send=send_message, company_check=None):
    """Send today's unsent fit jobs. Returns how many jobs were sent.

    Each company is checked once, the first time it arrives, so the message can say
    good, undecided, or not a company you want to work for.
    """
    env = read_env() if env is None else env
    if not env.get("TELEGRAM_BOT_TOKEN") or not env.get("TELEGRAM_CHAT_ID"):
        return 0
    day = day or date.today().isoformat()
    import applied_log  # imported here because applied_log imports daily_digest
    applied = [e.get("url") for e in applied_log.load_log().values()]
    sent = load_sent()
    # Yesterday too: the evening search runs after the last summary, and its jobs go out at 8:30 the next morning.
    jobs = pending_jobs(store, day, sent, applied, back=1)
    notices = take_notices()
    if not jobs and not notices:
        return 0
    shown = jobs[:MAX_JOBS_PER_RUN]
    check = company_standing.ensure if company_check is None else company_check
    for job in shown:
        try:
            check(job.get("company") or "")
        except Exception:
            pass
    try:
        for message in build_summary(notices, jobs, day):
            send(message, env)
    except Exception:
        raise  # the outbox file stays as .sending and goes out with the next summary
    done_notices()
    save_sent(sent | {j.get("url") for j in shown if j.get("url")})
    return len(shown)


def load_store_settled(tries=6, pause=20):
    """The job store, read again if a search is writing it at this moment (a half-written file is not JSON)."""
    for attempt in range(tries):
        try:
            return daily_digest.load_store()
        except ValueError:
            if attempt == tries - 1:
                raise
            time.sleep(pause)


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "digest"
    try:
        if cmd == "setup":
            wait = 0
            if "--wait" in args:
                wait = int(args[args.index("--wait") + 1]) if len(args) > args.index("--wait") + 1 else 180
            chat = find_chat_id(wait=wait)
            if not chat:
                print("No message found. Open t.me/Jobseekme_bot in Telegram, press Start, send any message, then run this again.")
                return 1
            save_env_value("TELEGRAM_CHAT_ID", chat)
            print("Chat id saved to .env.")
            return 0
        if cmd == "test":
            send_message("Job search bot is connected.")
            print("Sent.")
            return 0
        if cmd == "digest":
            store = load_store_settled()
            print(f"sent {notify_new_jobs(store)} job(s)")
            return 0
    except TelegramError as e:
        print(e)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
