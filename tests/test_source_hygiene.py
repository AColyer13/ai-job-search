"""A stray control character in source (a backspace where a regex meant `\\b`) is invisible in an editor and
silently stops a pattern from matching. Fail on any control character other than tab, newline and carriage return."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"\t", "\n", "\r"}


def test_no_control_characters_in_python_source():
    bad = []
    for path in sorted([*(ROOT / "job_scraper").glob("*.py"), *(ROOT / "tests").glob("*.py")]):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.split("\n"), 1):
            stray = sorted({hex(ord(c)) for c in line if ord(c) < 32 and c not in ALLOWED})
            if stray:
                bad.append(f"{path.relative_to(ROOT)}:{number} {', '.join(stray)}")
    assert not bad, "control characters in source: " + "; ".join(bad)
