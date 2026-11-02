"""Downstream conversion weight of each source, resume version, and application touchpoint.

    python job_scraper/attribution.py [--today 2026-11-20] [--min-n 25] [--paths 20]

A positive reply is the result, same as yield_report.py: an interview or an offer. What this adds is
the path. Each graded positive reply is the chain

    where the job was found  →  resume version  →  application touchpoint  →  positive reply

The touchpoint is the form or channel recorded on the tracker (Greenhouse, Ashby, and the rest).
Portfolio clicks and cold outreach are not columns on job_search_tracker.csv, so they are not invented
here.

The weight is effective connectivity, the same calculation connectome_interpreter documents for a
column of input proportions. The edge from an upstream group into the next group is that upstream
group's share of the next group's input, so each column sums to 1. The share three hops out is the
matching entry of A^3, two hops A^2, one hop A. Sparse multiplication is enough: this graph is the
tracker, not a connectome, and a column chunk of 1 is only there so the chunked product can be checked
against the full one.

On a chain where every positive reply has all three steps, those entries equal the group's counted
share of positive replies. Multiplying the collapsed edges can credit a combination that never
happened; the products cancel in the sum at the outcome, and they do not cancel on a shorter hop.
The shares below are the outcome hop only. The path list is the walks that were actually logged,
strongest first, stopped at a cap (the same idea as a path budget): each line's percent is that
chain's fraction of the positive replies, and the group share is the sum of its chains.

Non-replies stay out of the graph. Pooling them into the same touchpoint would change an upstream
share with paths that never reached a reply. They still count in the graded-application column, and
anything under 21 days stays out of both, for the same reason as the yield report. Nothing here
changes who gets applied to.
"""
import argparse
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yield_report  # noqa: E402

OUTCOME = "outcome:positive"
LAYERS = (
    ("Application source", "source", "discovery_source", 3),
    ("Resume version", "resume", "resume_version", 2),
    ("Application touchpoint", "touch", "channel", 1),
)


def multiply(left, right, threshold=0.0, chunk_columns=0):
    """Sparse product (left @ right)[row, col] = sum_k left[row, k] * right[k, col].

    `chunk_columns` splits the columns of `right` into chunks and multiplies one chunk at a time.
    The entries match a single product. A threshold above 0 drops results at or below it.
    """
    left_col = defaultdict(list)
    for (row, mid), weight in left.items():
        if weight:
            left_col[mid].append((row, weight))
    right_col = defaultdict(list)
    for (mid, col), weight in right.items():
        if weight:
            right_col[col].append((mid, weight))
    columns = sorted(right_col)
    size = chunk_columns if chunk_columns and chunk_columns > 0 else len(columns) or 1
    out = {}
    for start in range(0, len(columns), size):
        for col in columns[start:start + size]:
            terms = defaultdict(list)
            for mid, right_weight in right_col[col]:
                for row, left_weight in left_col.get(mid, ()):
                    terms[row].append(left_weight * right_weight)
            for row, parts in terms.items():
                value = math.fsum(parts)
                if value and not (threshold and abs(value) <= threshold):
                    out[(row, col)] = value
    return out


def powers(weights, steps, chunk_columns=0, root=False):
    """A^1 through A^steps. Rooting replaces each A^k entry with its k-th root after the product."""
    current = weights
    series = []
    for hop in range(1, steps + 1):
        if hop > 1:
            current = multiply(weights, current, chunk_columns=chunk_columns)
        if root:
            series.append({key: value ** (1 / hop) for key, value in current.items() if value > 0})
        else:
            series.append(current)
    return series


def column_normalize(counts):
    """Turn raw edge counts into input proportions. Every column with any input sums to 1."""
    totals = defaultdict(float)
    for (_pre, post), count in counts.items():
        totals[post] += count
    weights = {}
    for (pre, post), count in counts.items():
        total = totals[post]
        if total and count:
            weights[(pre, post)] = count / total
    return weights


def _label(row, field):
    return (row.get(field) or "").strip() or "unknown"


def _node(kind, label):
    return f"{kind}:{label}"


def _positive(row):
    return yield_report._status(row) in yield_report.POSITIVE


def conversion_edges(rows):
    """Count the three forward edges on each positive reply. Other rows are not edges."""
    counts = defaultdict(float)
    chains = defaultdict(list)
    for row in rows:
        if not _positive(row):
            continue
        steps = [_node(kind, _label(row, field)) for _title, kind, field, _hops in LAYERS]
        source, resume, touch = steps
        counts[(source, resume)] += 1
        counts[(resume, touch)] += 1
        counts[(touch, OUTCOME)] += 1
        chains[(source, resume, touch)].append(row)
    return counts, chains


def group_shares(series, kind, hops):
    """Outcome-column of A^hops for every node of this kind. Missing nodes are absent, not zero."""
    matrix = series[hops - 1]
    prefix = f"{kind}:"
    shares = defaultdict(float)
    for (pre, post), weight in matrix.items():
        if post == OUTCOME and pre.startswith(prefix):
            shares[pre[len(prefix):]] += weight
    return shares


def _pct(share):
    return f"{share:.1%}"


NAME_WIDTH = 40


def _table(title, names, shares, support, min_n):
    lines = [
        title,
        f"  {'group':<{NAME_WIDTH}}{'positive':>9}{'share':>8}{'graded':>8}",
    ]
    ranked = sorted(names, key=lambda name: (-shares.get(name, 0.0), -support[name][0], name))
    for name in ranked:
        graded, positive = support[name]
        note = "  thin: read as noise" if graded < min_n else ""
        lines.append(
            f"  {name[:NAME_WIDTH]:<{NAME_WIDTH}}{positive:>9}{_pct(shares.get(name, 0.0)):>8}{graded:>8}{note}"
        )
    return lines


def _chain_lines(chains, total, budget):
    ranked = sorted(chains.items(), key=lambda item: (-len(item[1]), item[0]))
    lines = ["Recorded paths, strongest first"]
    shown = ranked[:budget]
    for (source, resume, touch), members in shown:
        share = len(members) / total
        labels = " → ".join(node.split(":", 1)[1] for node in (source, resume, touch))
        extra = ""
        if len(members) == 1:
            row = members[0]
            who = " — ".join(
                part for part in ((row.get("company") or "").strip(), (row.get("role") or "").strip()) if part
            )
            when = (row.get("date") or "").strip()
            extra = "".join(piece for piece in (f"  {who}" if who else "", f"  {when}" if when else "") if piece)
        lines.append(f"  {_pct(share):>6}  {len(members):>3}  {labels}{extra}")
    hidden = len(ranked) - len(shown)
    if hidden:
        lines.append(f"  {hidden} more chains not shown.")
    return lines


def build_report(rows, today, min_n=25, path_budget=20, chunk_columns=0):
    matured, too_new, undated = yield_report.summarize(rows, today)
    positives = [row for row in matured if _positive(row)]
    early = sum(_positive(row) for row in too_new)
    early_clause = (
        "1 of them already has a positive reply"
        if early == 1 else f"{early} of them already have a positive reply"
    )
    out = [
        f"Attribution, {today.isoformat()}",
        "Each share is that group's fraction of positive replies (interview or offer) across the steps "
        "between the group and the reply. A step's weight is the upstream group's share of the next "
        "group's input, and the downstream weight is those weights multiplied along the chain. The chain "
        "is where the job was found, the resume version, and the application touchpoint. Portfolio clicks "
        "and cold outreach are not in the tracker.",
        f"{len(rows)} tracked. In the graph: {len(positives)} positive replies at least "
        f"{yield_report.MATURE_DAYS} days old, out of {len(matured)} graded. "
        f"Not graded: {len(too_new)} newer ({early_clause}).",
    ]
    if undated:
        if len(undated) == 1:
            out.append("1 row has no readable date and is left out.")
        else:
            out.append(f"{len(undated)} rows have no readable date and are left out.")
    out.append("")
    if not matured:
        out.append("Nothing is old enough to grade yet.")
        return "\n".join(out)

    counts, chains = conversion_edges(positives)
    shares_by_kind = {}
    if positives:
        series = powers(column_normalize(counts), 3, chunk_columns=chunk_columns)
        for _title, kind, _field, hops in LAYERS:
            shares_by_kind[kind] = group_shares(series, kind, hops)
        source_total = math.fsum(shares_by_kind["source"].values())
        if abs(source_total - 1) > 1e-6:
            out.append(f"Source shares add to {_pct(source_total)}, not 100%. Treat the table as incomplete.")
            out.append("")
    else:
        out.append("No positive reply is old enough to put in the graph, so every share is 0.")
        out.append("")

    support = {kind: defaultdict(lambda: [0, 0]) for _title, kind, _field, _hops in LAYERS}
    for row in matured:
        hit = _positive(row)
        for _title, kind, field, _hops in LAYERS:
            cell = support[kind][_label(row, field)]
            cell[0] += 1
            cell[1] += 1 if hit else 0

    for title, kind, _field, hops in LAYERS:
        out.extend(_table(
            f"{title}, {hops} hop{'s' if hops != 1 else ''} from a positive reply",
            support[kind],
            shares_by_kind.get(kind, {}),
            support[kind],
            min_n,
        ))
        out.append("")
    if positives:
        out.extend(_chain_lines(chains, len(positives), path_budget))
        out.append("")
    if len(matured) >= yield_report.MIN_APPLICATIONS_TO_DECIDE and len(positives) >= yield_report.MIN_POSITIVES_TO_DECIDE:
        verdict = (
            "Enough graded applications to compare large gaps in these shares. A share is not a reply rate; "
            "read yield_report.py before dropping a source, resume, or touchpoint."
        )
    else:
        verdict = (
            f"Not enough to decide anything. Wait for {yield_report.MIN_APPLICATIONS_TO_DECIDE}+ graded "
            f"applications and {yield_report.MIN_POSITIVES_TO_DECIDE}+ positive replies "
            f"(now {len(matured)} and {len(positives)}). "
            "Do not drop a source, change a resume, or drop a touchpoint from these shares."
        )
    out.append(verdict)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", help="ISO date to measure ages from, default today")
    ap.add_argument("--min-n", type=int, default=25, help="groups with fewer graded applications are marked thin")
    ap.add_argument("--paths", type=int, default=20, help="how many recorded chains to print")
    ap.add_argument("--tracker", type=Path, help="tracker CSV, default job_search_tracker.csv")
    args = ap.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print("error: --today must be an ISO date like 2026-11-20", file=sys.stderr)
        return 1
    print(build_report(yield_report.load_rows(args.tracker), today, args.min_n, max(0, args.paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
