"""Effective-connectivity shares follow the logged chain and stay inside the yield-report rules."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "job_scraper"))
import attribution  # noqa: E402

TODAY = date(2026, 11, 20)


def row(day, status="applied", **over):
    base = {
        "date": day, "status": status, "discovery_source": "board",
        "resume_version": "r1", "channel": "ashby", "company": "", "role": "",
    }
    base.update(over)
    return base


def line_starting(text, name):
    return next(line for line in text.splitlines() if line.strip().startswith(name))


def test_two_hop_product_matches_the_worked_example_and_root_is_applied_after():
    # A --0.4--> B --0.6--> C contributes 0.24. A second walk adds 0.10. The root is of the sum.
    weights = {("a", "b"): 0.4, ("b", "c"): 0.6, ("a", "d"): 0.2, ("d", "c"): 0.5}
    assert abs(attribution.multiply(weights, weights)[("a", "c")] - 0.34) < 1e-12
    rooted = attribution.powers(weights, 2, root=True)[1][("a", "c")]
    assert abs(rooted - (0.34 ** 0.5)) < 1e-12
    assert abs(rooted - ((0.4 ** 0.5) * (0.6 ** 0.5) + (0.2 ** 0.5) * (0.5 ** 0.5))) > 0.1


def test_self_reach_two_hops_is_the_column_picture_from_the_connectivity_notes():
    # (A @ A)[a, a] = 0*0 + 0.3*0.2 + 0.5*0.8 = 0.46
    weights = {("a", "b"): 0.3, ("b", "a"): 0.2, ("a", "c"): 0.5, ("c", "a"): 0.8}
    assert abs(attribution.multiply(weights, weights)[("a", "a")] - 0.46) < 1e-12


def test_a_column_that_sums_to_one_still_sums_to_one_after_three_hops_including_chunks():
    weights = {("a", "a"): 0.2, ("b", "a"): 0.8, ("a", "b"): 0.7, ("b", "b"): 0.3}
    full = attribution.powers(weights, 3)[2]
    chunked = attribution.powers(weights, 3, chunk_columns=1)[2]
    for col in ("a", "b"):
        assert abs(full[("a", col)] + full[("b", col)] - 1) < 1e-12
        assert abs(chunked[("a", col)] - full[("a", col)]) < 1e-12
    assert ("a", "c") not in attribution.multiply(
        {("a", "b"): 0.4, ("b", "c"): 0.6}, {("a", "b"): 0.4, ("b", "c"): 0.6}, threshold=0.3
    )


def test_outcome_share_is_the_counted_share_and_a_shorter_hop_is_not():
    apps = [("L", "v2", "G")] * 3 + [("I", "v1", "G")] + [("I", "v2", "A")] * 2
    counts = {}
    for source, resume, touch in apps:
        for edge in (
            (f"source:{source}", f"resume:{resume}"),
            (f"resume:{resume}", f"touch:{touch}"),
            (f"touch:{touch}", attribution.OUTCOME),
        ):
            counts[edge] = counts.get(edge, 0) + 1
    series = attribution.powers(attribution.column_normalize(counts), 3)
    # Touch G's positive replies are 3/4 from L, but the two-hop matrix entry is not that fraction.
    assert abs(series[1].get(("source:L", "touch:G"), 0) - 0.75) > 0.1
    assert abs(series[2][("source:L", attribution.OUTCOME)] - 0.5) < 1e-9
    assert abs(series[1][("resume:v2", attribution.OUTCOME)] - 5 / 6) < 1e-9
    assert abs(series[0][("touch:G", attribution.OUTCOME)] - 4 / 6) < 1e-9


def test_report_uses_those_shares_and_leaves_non_replies_out_of_the_graph():
    rows = [row("2026-10-01", "interview", discovery_source="L", resume_version="v2", channel="G") for _ in range(3)]
    rows.append(row("2026-10-01", "interview", discovery_source="I", resume_version="v1", channel="G"))
    rows += [row("2026-10-01", "interview", discovery_source="I", resume_version="v2", channel="A") for _ in range(2)]
    rows.append(row("2026-10-01", "rejected", discovery_source="I", resume_version="v2", channel="G"))
    text = attribution.build_report(rows, TODAY)
    assert line_starting(text, "L").split()[1:4] == ["3", "50.0%", "3"]
    assert line_starting(text, "I").split()[1:4] == ["3", "50.0%", "4"]
    assert line_starting(text, "v2").split()[1:4] == ["5", "83.3%", "6"]
    assert line_starting(text, "G").split()[1:4] == ["4", "66.7%", "5"]
    assert "50.0%    3  L → v2 → G" in text
    assert "thin: read as noise" in text
    assert "Not enough to decide anything" in text


def test_young_withdrawn_and_undated_rows_do_not_enter_the_graph():
    rows = [
        row("2026-10-01", "interview", discovery_source="oldboard", company="Socket", role="Sales Engineer"),
        row("2026-11-10", "interview", discovery_source="newboard"),
        row("2026-10-01", "withdrawn", discovery_source="oldboard"),
        row("not a date", "interview", discovery_source="nodate"),
    ]
    text = attribution.build_report(rows, TODAY)
    assert line_starting(text, "oldboard").split()[1:4] == ["1", "100.0%", "1"]
    assert "newboard" not in text and "nodate" not in text
    assert "Socket — Sales Engineer" in text and "2026-10-01" in text
    assert "1 row has no readable date and is left out." in text
    assert "Not graded: 1 newer (1 of them already has a positive reply)." in text


def test_no_positive_reply_keeps_every_share_at_zero():
    text = attribution.build_report([row("2026-10-01", "rejected", discovery_source="board")], TODAY)
    assert "every share is 0" in text
    assert line_starting(text, "board").split()[1:4] == ["0", "0.0%", "1"]
    assert "Recorded paths" not in text


def test_enough_graded_replies_changes_the_verdict_without_calling_a_share_a_rate():
    rows = [row("2026-10-01", "interview") for _ in range(10)]
    rows += [row("2026-10-01", "applied") for _ in range(90)]
    text = attribution.build_report(rows, TODAY, min_n=25)
    assert "Enough graded applications to compare large gaps" in text
    assert "A share is not a reply rate" in text
    assert "thin" not in text


def test_chunked_report_matches_the_full_product():
    rows = [row("2026-10-01", "offer", discovery_source="L", resume_version="v2", channel="G")]
    rows.append(row("2026-10-01", "interview only", discovery_source="I", resume_version="v1", channel="A"))
    assert attribution.build_report(rows, TODAY, chunk_columns=1) == attribution.build_report(rows, TODAY)
