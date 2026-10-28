# spike: does title-eligibility decay across FreeHire pages, i.e. is the 20-page cap dropping eligible jobs?
# data: smoke_run/fh_*.json from the 2026-10-27 live search (row order = page order, 50 rows/page)
import sys, json, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scoring
rd = Path(sys.argv[1])
bands = collections.defaultdict(lambda: [0, 0])  # page band -> [eligible, total]
lost = 0.0
for fp in sorted(rd.glob("fh_*.json")):
    d = json.load(open(fp, encoding="utf-8"))
    rows, total = d["results"], d["meta"].get("total") or 0
    if len(rows) < 1000:
        continue  # only queries that hit the cap
    flags = [scoring.score({"title": r.get("title"), "description": ""})["grade"] != "low" for r in rows]
    for i, ok in enumerate(flags):
        b = bands[f"p{i // 250 * 5 + 1:02}-{i // 250 * 5 + 5:02}"]
        b[0] += ok; b[1] += 1
    tail = sum(flags[750:]) / 250
    lost += tail * max(total - 1000, 0)
    print(f"{fp.stem:24} total={total:5} eligible p1-5={sum(flags[:250])/2.5:4.0f}% p16-20={tail*100:4.0f}% est unread eligible={tail*max(total-1000,0):5.0f}")
for k, (ok, n) in sorted(bands.items()):
    print(f"{k}: {ok}/{n} = {100 * ok / n:.1f}%")
print(f"est eligible rows beyond the cap (before dedupe): {lost:.0f}")
