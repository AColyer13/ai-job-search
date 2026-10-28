import sys, json, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
names = ["fh_software_developer", "fh_customer_engineer", "fh_solutions", "fh_solutions_architect"]
qs = {q["name"]: q for q in json.load(open(pipeline.QUERIES_PATH, encoding="utf-8"))["cli_queries"]}
def run(name):
    base = pipeline._set_flag(pipeline._set_flag(qs[name]["args"], "--limit", 50), "--jobage", 3)
    rows, errs = [], []
    for page in range(21, 41):
        rc, o, e = pipeline._cli_page("freehire-search", pipeline._set_flag(base, "--page", page))
        try:
            data = json.loads(o)
        except Exception as ex:
            errs.append(f"page {page}: rc={rc} {ex} {e[:200]}"); break
        got = data.get("results") or []
        rows += got
        if len(got) < 50: break
        time.sleep(1.0)
    json.dump({"results": rows}, open(out / f"zz_deep_{name}.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(name, "rows", len(rows), "errors", errs, flush=True)
with ThreadPoolExecutor(4) as ex: list(ex.map(run, names))
