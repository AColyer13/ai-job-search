"""Tests for the /html-report command and its gitignore rule.

Mirrors the pattern in test_security_guards.py: one class that verifies
properties of the real repo, testing the things CI would catch if the
command file or gitignore rule were wrong.
"""

import subprocess
import sys
import unittest
from pathlib import Path

try:
    import yaml  # noqa: F401 - only probing availability for the lint integration test
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMAND_FILE = REPO_ROOT / ".claude" / "commands" / "html-report.md"
LINT_SCRIPT = REPO_ROOT / "tools" / "lint_skills.py"
GITIGNORE = REPO_ROOT / ".gitignore"


class HtmlReportCommandFileTests(unittest.TestCase):
    """Structural checks on the command file itself."""

    def test_command_file_exists(self):
        self.assertTrue(COMMAND_FILE.exists(), f"{COMMAND_FILE} not found")

    def test_command_file_starts_with_correct_header(self):
        """lint_skills.py rejects command files that don't start with '# /<name>'."""
        text = COMMAND_FILE.read_text(encoding="utf-8")
        first_line = text.lstrip().splitlines()[0]
        self.assertTrue(
            first_line.startswith("# /html-report"),
            f"Command file must start with '# /html-report', got: {first_line!r}",
        )

    def test_command_file_is_non_empty(self):
        text = COMMAND_FILE.read_text(encoding="utf-8").strip()
        self.assertGreater(len(text), 100, "Command file appears suspiciously short")


class HtmlReportGitignoreTests(unittest.TestCase):
    """reports/ must be gitignored — it holds personal generated output."""

    def test_reports_folder_is_gitignored(self):
        rules = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}
        self.assertIn(
            "reports/",
            rules,
            "reports/ must be listed in .gitignore — generated dashboards are personal output",
        )


@unittest.skipUnless(
    _HAVE_YAML,
    "PyYAML not installed (the CI Python-test job omits it; the lint job runs lint_skills.py directly)",
)
class HtmlReportLintIntegrationTests(unittest.TestCase):
    """lint_skills.py must pass after the command is added."""

    def test_lint_passes_on_real_repo(self):
        result = subprocess.run(
            [sys.executable, str(LINT_SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"lint_skills.py failed:\n{result.stdout}{result.stderr}",
        )
        self.assertIn("OK", result.stdout)


class HtmlReportRenderTests(unittest.TestCase):
    """The generator escapes store text and names the tracker gap."""

    def test_job_is_present_and_script_payload_is_escaped(self):
        sys.path.insert(0, str(REPO_ROOT / "job_scraper"))
        import html_report

        title = "<script>alert(1)</script>"
        page = html_report.render_page(
            [{
                "title": title,
                "fit": "high",
                "company": "Example",
                "location": "Remote",
                "work_mode": "remote",
                "url": "https://example.com/job",
                "found_on": "2026-10-20",
                "body_status": "opened",
                "description": "Sell the product to plant managers.",
            }],
            [],
            [{"slug": "alignerr_aitraining"}],
            ["SKIP adzuna (set ADZUNA_APP_ID and ADZUNA_APP_KEY to enable)"],
            "2026-10-20",
        )
        self.assertIn("Sell the product to plant managers.", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("alignerr_aitraining", page)
        self.assertIn("SKIP adzuna", page)
        self.assertIn("No jobs in this file.", html_report.render_page([], [], [], [], "2026-10-20"))


if __name__ == "__main__":
    unittest.main()
