"""Write reports/application-dashboard.html from the same list as latest.docx.

The daily publish calls write_dashboard. Running this file does the same
write without a scrape and without pruning the store.
"""
import csv
import importlib.util
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "application-dashboard.html"
TRACKER_PATH = ROOT / "job_search_tracker.csv"

if str(ROOT / "job_scraper") not in sys.path:
    sys.path.insert(0, str(ROOT / "job_scraper"))


def esc(value):
    return escape("" if value is None else str(value), quote=True)


def load_tracker_rows():
    if not TRACKER_PATH.exists():
        return []
    with TRACKER_PATH.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_drift():
    """Drafted CV or cover files with no tracker row. Nothing is invented."""
    path = ROOT / "tools" / "check_tracker_drift.py"
    spec = importlib.util.spec_from_file_location("check_tracker_drift", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing, tracker_rows = mod.find_drift()
    return missing, tracker_rows


def _card(job, number, type_key):
    import daily_digest

    fit = (job.get("fit") or "low").lower()
    title = job.get("title") or "Untitled"
    company = job.get("company") or "Company not listed"
    where = daily_digest._where(job)
    why = daily_digest._why(job)
    hits = job.get("star_notes") or []
    star = "★ " if hits else ""
    blob = " ".join([title, company, where, why, fit]).lower()
    pay = job.get("salary") or ""
    url = daily_digest.apply_link(job)
    minimums = daily_digest._minimums_line(job)
    parts = [
        f'<article class="card" data-job data-fit="{esc(fit)}" data-type="{esc(type_key)}" data-text="{esc(blob)}">',
        f"<h3>{esc(star + str(number) + '. ' + fit.upper() + ' | ' + title)}</h3>",
        f"<p>{esc(company)} | {esc(where)}</p>",
    ]
    if hits:
        labels = ", ".join(hit.get("label") or "" for hit in hits)
        parts.append(f"<p class=\"star\">★ Posting lists: {esc(labels)}</p>")
        for hit in hits:
            quote = (hit.get("quote") or "").strip()
            if quote:
                parts.append(f"<p>{esc(quote)}</p>")
    if pay:
        parts.append(f"<p>Pay on the card: {esc(pay)}</p>")
    parts.append(f"<p>{esc(why)}</p>")
    if minimums:
        parts.append(f"<p class=\"minimums\">{esc(minimums)}</p>")
    if url:
        parts.append(f'<p><a href="{esc(url)}">{esc(url)}</a></p>')
    parts.append("</article>")
    return "\n".join(parts)


def _sections(groups):
    blocks = []
    for section in groups:
        cards = []
        for number, job in enumerate(section["jobs"], 1):
            cards.append(_card(job, number, section["key"]))
        blocks.append(
            "<section data-section>\n"
            f"<h2>{esc(section['label'])}</h2>\n"
            + "\n".join(cards)
            + "\n</section>"
        )
    return "\n".join(blocks)


def _applications(rows):
    if not rows:
        return "<p>No tracker rows yet.</p>"
    blocks = []
    for row in rows:
        company = row.get("company") or "Company not listed"
        role = row.get("role") or "Role not listed"
        date = row.get("date") or ""
        status = row.get("status") or ""
        notes = row.get("notes") or ""
        blocks.append(
            "<article class=\"app\">"
            f"<h3>{esc(company)} — {esc(role)}</h3>"
            f"<p>{esc(date)} · {esc(status)}</p>"
            + (f"<p>{esc(notes)}</p>" if notes else "")
            + "</article>"
        )
    return "\n".join(blocks)


def _drift(missing):
    if not missing:
        return "<p>Every drafted application has a tracker row.</p>"
    items = "\n".join(f"<li>{esc(item.get('slug') or '')}</li>" for item in missing)
    return (
        "<p>These files exist. The tracker has no row for them. "
        "Dates and status are not filled in here.</p>\n"
        f"<ul>\n{items}\n</ul>"
    )


def render_page(jobs, rows, drift, sources, day):
    """One offline page. Job text is escaped. The script block is static."""
    import daily_digest

    upper, lows = daily_digest._split_for_doc(jobs)
    counts = daily_digest._counts([j for j in daily_digest.listable(jobs) if not daily_digest.is_unchecked(j)])
    total = sum(counts.values())
    blocks = []
    if upper:
        blocks.append("<h2>High and medium by type</h2>\n" + _sections(upper))
    if lows:
        blocks.append("<h2>Low</h2>\n" + _sections(lows))
    job_html = "\n".join(blocks) if blocks else "<p>No jobs in this file.</p>"
    source_html = ""
    if sources:
        items = "\n".join(f"<li>{esc(line)}</li>" for line in sources)
        source_html = f"<h2>Latest search</h2>\n<ul>\n{items}\n</ul>"
    type_options = "\n".join(
        f'<option value="{esc(key)}">{esc(label)}</option>'
        for key, label in daily_digest.TYPE_ORDER
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adam Colyer job search</title>
<style>
html, body {{ overflow-x: clip; margin: 0; }}
body {{ font: 16px/1.45 system-ui, sans-serif; color: #1c1915; background: #f3efe6; }}
main {{ max-width: 52rem; margin: 0 auto; padding: 1.25rem; }}
h1 {{ font-size: 1.6rem; line-height: 1.2; margin: 0 0 0.25rem; }}
h2 {{ font-size: 1.15rem; margin: 1.5rem 0 0.6rem; }}
h3 {{ font-size: 1.02rem; margin: 0 0 0.35rem; overflow-wrap: anywhere; }}
p, li {{ overflow-wrap: anywhere; }}
a {{ color: #1d4e89; }}
.filters {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 1rem 0; }}
.filters input, .filters select {{ font: inherit; min-width: 0; flex: 1 1 12rem; padding: 0.45rem 0.55rem; }}
.card, .app {{ background: #fff; border: 1px solid #ddd4c6; border-left: 4px solid #8a8175; padding: 0.85rem 1rem; margin: 0 0 0.75rem; }}
.card[data-fit="high"] {{ border-left-color: #1f7a4d; }}
.card[data-fit="medium"] {{ border-left-color: #a15c07; }}
.card[data-fit="low"] {{ border-left-color: #8a8175; }}
.star {{ font-weight: 600; }}
[hidden] {{ display: none !important; }}
@media (max-width: 700px) {{
  .filters {{ display: block; }}
  .filters input, .filters select {{ width: 100%; box-sizing: border-box; margin: 0 0 0.5rem; }}
}}
</style>
</head>
<body>
<main>
<h1>Adam Colyer job search</h1>
<p>Latest jobs as of {esc(day)}</p>
<p>Jobs: {total}    High: {counts['high']}    Medium: {counts['medium']}    Low: {counts['low']}</p>
<p>Grades come from the posting text, not the title. A job board row whose text could not be read is not listed.</p>
{source_html}
<div class="filters">
<label>Search <input id="q" type="search" autocomplete="off"></label>
<label>Grade
<select id="fit">
<option value="">All grades</option>
<option value="high">High</option>
<option value="medium">Medium</option>
<option value="low">Low</option>
</select>
</label>
<label>Type
<select id="type">
<option value="">All types</option>
{type_options}
</select>
</label>
</div>
{job_html}
<h2>Applications</h2>
{_applications(rows)}
<h2>Drafts with no tracker row</h2>
{_drift(drift)}
<p>Generated by job_scraper/html_report.py · ai-job-search · {esc(day)}</p>
</main>
<script>
function applyFilters() {{
  var q = document.getElementById("q").value.toLowerCase();
  var fit = document.getElementById("fit").value;
  var type = document.getElementById("type").value;
  document.querySelectorAll("[data-job]").forEach(function (card) {{
    var text = card.getAttribute("data-text") || "";
    var show = (!q || text.indexOf(q) !== -1)
      && (!fit || card.getAttribute("data-fit") === fit)
      && (!type || card.getAttribute("data-type") === type);
    card.hidden = !show;
  }});
  document.querySelectorAll("[data-section]").forEach(function (section) {{
    section.hidden = !section.querySelector("[data-job]:not([hidden])");
  }});
}}
document.getElementById("q").addEventListener("input", applyFilters);
document.getElementById("fit").addEventListener("change", applyFilters);
document.getElementById("type").addEventListener("change", applyFilters);
</script>
</body>
</html>
"""


def write_dashboard(store, sources, day, path=None, rows=None, drift=None):
    """Write the page for this store. Caller applies location caps and shells first."""
    import daily_digest

    jobs = daily_digest.listable(list(store.values()))
    if rows is None or drift is None:
        file_rows = load_tracker_rows()
        missing, _count = load_drift()
        if rows is None:
            rows = file_rows
        if drift is None:
            drift = missing
    path = Path(path) if path else REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_page(jobs, rows, drift, sources or [], day), encoding="utf-8")
    return path


def main():
    import daily_digest

    store = daily_digest.load_store()
    jobs = list(store.values())
    daily_digest.apply_location_caps(jobs)
    daily_digest.apply_board_shells(jobs)
    sources = daily_digest.load_sources()
    daily_digest.save_store(store, sources)
    found = [job.get("found_on") for job in jobs if job.get("found_on")]
    day = max(found) if found else "latest"
    path = write_dashboard(store, sources, day)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
