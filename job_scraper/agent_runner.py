"""Run one prompt through a headless AI agent, trying providers in order until one works.

    python job_scraper/agent_runner.py "Reply with exactly: OK"
    python job_scraper/agent_runner.py --providers grok "Reply with exactly: OK"

Providers (AGENT_PROVIDERS in .env, default "grok,antigravity"):

    grok     Grok Build CLI (`grok -p -m GROK_MODEL`), signed in with grok.com. GROK_MODEL in .env,
             default grok-4.7. It has to be a cloud Grok model: the CLI's own default is a local Ollama
             model, which is the "local" tier below. Tried first unless AGENT_PROVIDERS says otherwise.
    antigravity  Google's Antigravity CLI (`agy -p ... --mode plan`), the replacement for the Gemini CLI, which
             Google no longer lets sign in. Installed at %LOCALAPPDATA%\agy\bin\agy.exe. Optional AGY_MODEL in
             .env; unset uses the CLI's default. "agy" and "gemini" in AGENT_PROVIDERS mean this provider.
    claude   Claude Code headless (`claude -p`). Off unless named in AGENT_PROVIDERS. ANTHROPIC_API_KEY is
             removed from the child's environment so a stray key can never switch it to paid API billing.
    local    The Grok CLI's local default model (Ollama). Off unless named in AGENT_PROVIDERS, because a
             20B model is not reliable at driving a browser form.

A provider counts as failed on a non-zero exit, an error reply, empty text, or a timeout, and the next
one runs. If all fail, the result says so and the caller leaves the job for Adam to do by hand.
"""
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_digest  # noqa: E402
import telegram_notify  # noqa: E402

ROOT = daily_digest.ROOT
CLAUDE = Path.home() / ".local" / "bin" / "claude.exe"
GROK = Path.home() / ".grok" / "bin" / "grok.exe"
AGY = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
MAX_ARG_CHARS = 28000  # Windows allows about 32,000 characters on a command line
DEFAULT_PROVIDERS = ("grok", "antigravity")
DEFAULT_TIMEOUT = 600


@dataclass
class Result:
    ok: bool
    provider: str = ""
    text: str = ""
    attempts: list = field(default_factory=list)  # [(provider, why it failed)]


class ProviderFailed(Exception):
    pass


def _exe(path, name):
    return str(path) if path.exists() else name


def _run(cmd, cwd, env, timeout, stdin=None):
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), env=env, input=stdin, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ProviderFailed(f"timed out after {timeout}s") from None
    except OSError as e:
        raise ProviderFailed(f"could not start: {type(e).__name__}") from None


def _json_reply(proc):
    """The JSON object a CLI printed, or None."""
    out = (proc.stdout or "").strip()
    for candidate in (out, out[out.find("{"):] if "{" in out else ""):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def run_claude(prompt, cwd, timeout, max_turns, allowed_tools=None, **_):
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [_exe(CLAUDE, "claude"), "-p", "--output-format", "json", "--max-turns", str(max_turns),
           "--no-session-persistence"]
    if allowed_tools is not None:
        # [] means no tools at all: the model may only write text. None leaves Claude Code's defaults.
        cmd += ["--allowedTools", ",".join(allowed_tools)] if allowed_tools else ["--tools", ""]
    proc = _run(cmd, cwd, env, timeout, stdin=prompt)
    data = _json_reply(proc)
    if proc.returncode != 0 or data is None or data.get("is_error"):
        why = (data or {}).get("result") or (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise ProviderFailed(str(why)[:200])
    return str(data.get("result") or "")


def run_grok(prompt, cwd, timeout, max_turns, model=None, **_):
    env = os.environ.copy()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        prompt_file = fh.name
    try:
        cmd = [_exe(GROK, "grok"), "--prompt-file", prompt_file, "--max-turns", str(max_turns),
               "--output-format", "json"]
        if model:
            cmd += ["-m", model]
        proc = _run(cmd, cwd, env, timeout)
    finally:
        Path(prompt_file).unlink(missing_ok=True)
    data = _json_reply(proc)
    if proc.returncode != 0 or data is None or data.get("type") == "error":
        why = (data or {}).get("message") or (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise ProviderFailed(str(why).replace("\n", " ")[:200])
    return str(data.get("text") or "")


def run_antigravity(prompt, cwd, timeout, max_turns, model=None, mode="plan", **_):
    """Google's Antigravity CLI (`agy`, which replaced the Gemini CLI), headless.

    `agy` ignores a prompt on stdin (it tries to run a command to read it), so the prompt is a command-line
    argument, which Windows caps near 32,000 characters. Plan mode is read-only and headless mode cannot approve
    a tool, so a write or shell command is refused. `auto` is for a job that has to run commands, such as the
    Gmail-sync fallback. --dangerously-skip-permissions is never used.
    """
    if len(prompt) > MAX_ARG_CHARS:
        raise ProviderFailed(f"prompt is {len(prompt)} characters, too long for the command line")
    if mode not in ("plan", "auto", "accept-edits"):
        raise ProviderFailed(f"unknown mode {mode}")
    cmd = [_exe(AGY, "agy"), "-p", prompt, "--output-format", "json", "--mode", mode]
    if model:
        cmd += ["--model", model]
    proc = _run(cmd, cwd, os.environ.copy(), timeout)
    data = _json_reply(proc)
    if proc.returncode != 0 or data is None or data.get("status") != "SUCCESS":
        why = (data or {}).get("error") or (data or {}).get("status") or (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise ProviderFailed(str(why.get("message") if isinstance(why, dict) else why).replace("\n", " ")[:200])
    text = str(data.get("response") or "")
    if not text.strip() and data.get("denied_actions"):
        raise ProviderFailed("it needed a tool that headless mode cannot approve")
    return text


def provider_chain(env=None):
    env = env or telegram_notify.read_env()
    raw = os.environ.get("AGENT_PROVIDERS") or env.get("AGENT_PROVIDERS") or ""
    names = [n.strip().lower() for n in raw.split(",") if n.strip()] or list(DEFAULT_PROVIDERS)
    model = env.get("GROK_MODEL") or "grok-4.7"
    agy_model = env.get("AGY_MODEL") or None  # unset: the CLI picks its own default
    names = ["antigravity" if n in ("agy", "gemini") else n for n in names]  # the Gemini CLI no longer signs in
    table = {"claude": run_claude, "grok": lambda *a, **k: run_grok(*a, model=model, **k),
             "antigravity": lambda *a, **k: run_antigravity(*a, model=agy_model, **k), "local": run_grok}
    return [(n, table[n]) for n in names if n in table]


def run_agent(prompt, cwd=None, timeout=DEFAULT_TIMEOUT, max_turns=1, allowed_tools=None,
              providers=None):
    """Try each provider in order. Returns a Result; never raises for a provider failure."""
    cwd = Path(cwd or ROOT)
    result = Result(ok=False)
    for name, run in (providers if providers is not None else provider_chain()):
        try:
            text = run(prompt, cwd=cwd, timeout=timeout, max_turns=max_turns, allowed_tools=allowed_tools)
        except ProviderFailed as e:
            result.attempts.append((name, str(e)))
            continue
        if not text.strip():
            result.attempts.append((name, "empty reply"))
            continue
        result.ok, result.provider, result.text = True, name, text
        return result
    return result


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    providers = None
    if args[:1] == ["--providers"]:
        env = {"AGENT_PROVIDERS": args[1]}
        os.environ["AGENT_PROVIDERS"] = env["AGENT_PROVIDERS"]
        args = args[2:]
    if not args:
        print(__doc__)
        return 2
    result = run_agent(" ".join(args), cwd=Path(tempfile.gettempdir()), providers=providers)
    for name, why in result.attempts:
        print(f"{name} failed: {why}")
    if result.ok:
        print(f"{result.provider}: {result.text}")
        return 0
    print("every provider failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
