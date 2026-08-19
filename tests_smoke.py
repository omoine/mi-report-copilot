"""Smoke tests: exercise the deterministic layers without needing an API key.

Run: .venv\\Scripts\\python.exe tests_smoke.py
"""

from pathlib import Path
import os
import sys

# Pin the tests to the small fixed sample: the row-count assertions below
# describe that file, and the app defaults to the generated month.
os.environ.setdefault(
    "DATA_FILE",
    str(Path(__file__).parent / "data" / "synthetic_liquidity_views.xlsx"),
)

from app import data_access, md_export, pdf_export, report_builder

OUT = Path(__file__).parent / "exports"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(label)


print("\n1. Loading views")
for view in ("nostro_transfer", "client", "business_ledger"):
    df = data_access.get_frame(view)
    check(f"{view} loaded", not df.empty, f"{len(df)} rows x {len(df.columns)} cols")
    check(f"{view} excludes Total footer", "Total" not in df.iloc[:, 0].astype(str).values)

expected = {"nostro_transfer": 37, "client": 24, "business_ledger": 60}
for view, n in expected.items():
    actual = len(data_access.get_frame(view))
    check(f"{view} row count == {n} (per workbook README)", actual == n, f"got {actual}")

print("\n2. Derived (formula) columns carry cached values")
client = data_access.get_frame("client")
check("Calculated Balance (Local) populated", client["Calculated Balance (Local)"].notna().all())
check("Match column populated", client["Match"].notna().all(),
      f"values: {sorted(set(client['Match'].astype(str)))}")

print("\n3. Metadata for grounding limitations")
meta = data_access.get_metadata()
check("data dictionary rows", len(meta["data_dictionary"]) > 40, f"{len(meta['data_dictionary'])}")
check("view controls rows", len(meta["view_controls"]) > 20, f"{len(meta['view_controls'])}")
check("fx rates", "JPY" in meta["fx_rates"], f"{len(meta['fx_rates'])} currencies")

print("\n4. Schema summary (what the model sees)")
schema = data_access.schema_summary()
check("all three views in schema", len(schema) == 3)
ccy = next(c for c in schema["nostro_transfer"]["columns"] if c["name"] == "Currency")
check("enum domains surfaced", "values" in ccy, f"Currency: {ccy.get('values')}")

print("\n5. Deterministic queries")
r1 = data_access.run_query("nostro_transfer", group_by=["Currency"],
                           measure="Value Amount", aggregation="sum")
check("group-by query returns rows", not r1["table"].empty, f"{len(r1['table'])} groups")
check("provenance recorded", r1["provenance"]["rows_in_view"] == 37)

r2 = data_access.run_query("client", filters=[{"column": "Match", "operator": "eq", "value": "✕"}],
                           aggregation="count")
check("filter + count works", not r2["table"].empty,
      f"unmatched accounts: {r2['table'].iloc[0, 0]}")

# A model cannot reliably emit the tick/cross glyphs, so words must work too.
r2b = data_access.run_query("client",
                            filters=[{"column": "Match", "operator": "eq", "value": "unmatched"}],
                            aggregation="count")
check("word alias matches the glyph", r2b["table"].iloc[0, 0] == r2["table"].iloc[0, 0],
      f"alias gave {r2b['table'].iloc[0, 0]}, glyph gave {r2['table'].iloc[0, 0]}")

# A junk value must error, not silently return zero rows that read as a finding.
try:
    data_access.run_query("client",
                          filters=[{"column": "Match", "operator": "eq", "value": "\x7f"}],
                          aggregation="count")
    check("junk glyph value raises", False, "no error raised")
except data_access.QueryError as exc:
    check("junk glyph value raises rather than returning zero rows", True, str(exc)[:70])

r3 = data_access.run_query("business_ledger", group_by=["Cashflow Type"],
                           measure="Amount (Display)", aggregation="sum")
check("ledger group-by works", len(r3["table"]) > 1, f"{len(r3['table'])} types")

print("\n5b. List mode (show me the records)")
lst = data_access.run_query(
    "client", mode="list",
    filters=[{"column": "Match", "operator": "eq", "value": "unmatched"}],
    columns=["Account", "Account Name", "Difference (Local)"],
    sort_by="Difference (Local)", sort_desc=True)
check("list returns rows not a count", len(lst["table"]) == 3, f"{len(lst['table'])} rows")
check("list returns requested columns",
      list(lst["table"].columns) == ["Account", "Account Name", "Difference (Local)"],
      str(list(lst["table"].columns)))
check("list mode recorded in provenance", lst["provenance"]["mode"] == "list")

top = data_access.run_query("business_ledger", mode="list",
                            columns=["Transaction Reference", "Amount (Display)"],
                            sort_by="Amount (Display)", sort_desc=True, limit=10)
check("top-N limit applied", len(top["table"]) == 10, f"{len(top['table'])} rows")
vals = top["table"]["Amount (Display)"].tolist()
check("top-N actually sorted descending", vals == sorted(vals, reverse=True))

print("\n5c. Time bucketing (the intraday profile)")
raw = data_access.run_query("business_ledger", group_by=["Transaction Timestamp"],
                            measures=[{"column": "Amount (Display)", "aggregation": "sum"}])
hourly = data_access.run_query(
    "business_ledger",
    time_bucket={"column": "Transaction Timestamp", "granularity": "hour"},
    measures=[{"column": "Amount (Display)", "aggregation": "sum"}])
check("raw timestamp grouping is degenerate (why bucketing exists)",
      len(raw["table"]) > len(hourly["table"]),
      f"raw={len(raw['table'])} groups vs hourly={len(hourly['table'])}")
multi = (hourly["table"].iloc[:, 1] != 0).sum()
check("hourly bucketing reduces group count",
      len(hourly["table"]) < len(raw["table"]),
      f"{len(raw['table'])} -> {len(hourly['table'])} buckets ({multi} non-zero). "
      "NOTE: the sample data is too thin for a useful intraday profile - "
      "see DATA_REQUIREMENTS.md")
check("bucket column is labelled", "Transaction Timestamp (hour)" in hourly["table"].columns,
      str(list(hourly["table"].columns)))

daily = data_access.run_query(
    "nostro_transfer",
    time_bucket={"column": "Created Time", "granularity": "day"},
    measures=[{"column": "Value Amount", "aggregation": "sum"}])
check("day granularity works", len(daily["table"]) >= 1, f"{len(daily['table'])} days")

try:
    data_access.run_query("client", time_bucket={"column": "Account Name",
                                                 "granularity": "hour"},
                          measures=[{"column": "Credits (Local)"}])
    check("bucketing a non-timestamp raises", False, "no error")
except data_access.QueryError as exc:
    check("bucketing a non-timestamp raises", True, str(exc)[:55])

print("\n5d. Multi-measure comparison")
cmp_result = data_access.run_query(
    "client", group_by=["Currency"],
    measures=[{"column": "Credits (Display)", "aggregation": "sum"},
              {"column": "Debits (Display)", "aggregation": "sum"}])
check("both measures present",
      cmp_result["measure_columns"] == ["Credits (Display)", "Debits (Display)"],
      str(cmp_result["measure_columns"]))
check("grouped by currency", cmp_result["label_columns"] == ["Currency"])
check("comparison table has 3 columns", len(cmp_result["table"].columns) == 3,
      str(list(cmp_result["table"].columns)))

grouped_chart = report_builder.build_chart(
    cmp_result["table"], "bar", "Credits vs Debits", OUT, "smoke",
    measure_columns=cmp_result["measure_columns"],
    label_columns=cmp_result["label_columns"])
check("grouped bar chart renders",
      grouped_chart["chart_path"] and grouped_chart["chart_path"].exists())

print("\n5e. Distribution / statistics")
dist = data_access.run_query("nostro_transfer", mode="distribution",
                             measures=[{"column": "Value Amount"}])
stats = dict(zip(dist["table"]["Statistic"], dist["table"]["Value"]))
check("distribution returns the standard statistics",
      {"mean", "median", "std", "p25", "p75", "p95"} <= set(stats),
      f"{len(stats)} statistics")
check("sigma bounds reported",
      "mean_minus_3std" in stats and "mean_plus_3std" in stats)
check("actual coverage reported (not assumed normal)",
      0 <= stats["within_1std_pct"] <= 100,
      f"within 1 sigma: {stats['within_1std_pct']:.0f}% "
      f"(normal would be ~68%), skew={stats['skew']:.2f}")
check("median differs from mean on skewed data", stats["mean"] != stats["median"],
      f"mean={stats['mean']:,.0f} median={stats['median']:,.0f}")
check("raw values carried for plotting", dist["raw_values"] is not None,
      f"{len(dist['raw_values'])} values")

hist = report_builder.render_distribution(
    dist["raw_values"], "Value Amount", "Distribution of Value Amount", OUT, "smoke")
check("histogram renders", hist["chart_path"] and hist["chart_path"].exists())
check("histogram reports real sigma coverage",
      any("standard deviation" in n for n in hist["notes"]),
      hist["notes"][0][:80] if hist["notes"] else "no notes")

grouped_dist = data_access.run_query("nostro_transfer", mode="distribution",
                                     measures=[{"column": "Value Amount"}],
                                     group_by=["Currency"])
check("grouped distribution returns one row per group",
      len(grouped_dist["table"]) > 1, f"{len(grouped_dist['table'])} groups")
box = report_builder.render_distribution(
    grouped_dist["raw_values"], "Value Amount", "Value by currency", OUT, "smoke",
    group_col="Currency")
check("box plot renders", box["chart_path"] and box["chart_path"].exists())

pct = data_access.run_query("business_ledger", group_by=["Cashflow Type"],
                            measures=[{"column": "Amount (Display)", "aggregation": "p95"},
                                      {"column": "Amount (Display)", "aggregation": "median"}])
check("percentile aggregations work", len(pct["table"].columns) >= 2,
      str(list(pct["table"].columns)))

print("\n5f. Analysis functions")
der = data_access.run_query(
    "client", mode="list",
    derived=[{"name": "Net movement", "left": "Credits (Local)",
              "op": "+", "right": "Debits (Local)"}],
    columns=["Account", "Credits (Local)", "Debits (Local)", "Net movement"])
check("derived column added", "Net movement" in der["table"].columns,
      str(list(der["table"].columns)))
check("derived column recorded in provenance",
      "Net movement" in der["provenance"]["derived"])

# A derived column must be usable as a filter, which means it is computed first.
der_f = data_access.run_query(
    "client", mode="list",
    derived=[{"name": "Variance", "left": "Calculated Balance (Local)",
              "op": "-", "right": "EOD Balance (Local)"}],
    filters=[{"column": "Variance", "operator": "ne", "value": 0}],
    columns=["Account", "Variance"])
check("derived column is filterable", len(der_f["table"]) < 24,
      f"{len(der_f['table'])} rows with a non-zero variance")

share = data_access.run_query("business_ledger", group_by=["Cashflow Type"],
                              measures=[{"column": "Amount (Display)"}],
                              add_share_of_total=True)
share_col = [c for c in share["table"].columns if c.startswith("% of total")]
check("share of total added", bool(share_col), str(share_col))
if share_col:
    check("shares are a percentage", abs(share["table"][share_col[0]].sum()) > 0,
          f"sums to {share['table'][share_col[0]].sum():.1f}")

cum = data_access.run_query(
    "business_ledger",
    time_bucket={"column": "Transaction Timestamp", "granularity": "hour"},
    measures=[{"column": "Amount (Display)"}],
    sort_by="Transaction Timestamp (hour)", sort_desc=False,
    add_cumulative=True)
cum_col = [c for c in cum["table"].columns if c.startswith("Cumulative")]
check("cumulative column added", bool(cum_col), str(cum_col))
if cum_col:
    vals = cum["table"][cum_col[0]].tolist()
    check("cumulative is a running total",
          abs(vals[-1] - cum["table"]["Amount (Display)"].sum()) < 0.01,
          f"ends at {vals[-1]:,.0f}")

print("\n5g. Combine / vlookup")
try:
    data_access.run_query("business_ledger", mode="list",
                          join={"view": "client", "on": {"left": "Account", "right": "Account"},
                                "bring": ["Legal Entity"]},
                          columns=["Account", "Legal Entity"])
    check("mismatched keys raise rather than return empty", False, "no error raised")
except data_access.QueryError as exc:
    check("mismatched keys raise rather than return empty",
          "no values in common" in str(exc), str(exc)[:80])

# A join on keys that DO overlap must work - prove it with a self-join.
self_join = data_access.run_query(
    "client", mode="list",
    join={"view": "client", "on": {"left": "Account", "right": "Account"},
          "bring": ["Legal Entity"]},
    columns=["Account"], limit=5)
check("join works when keys overlap", len(self_join["table"]) == 5,
      f"{len(self_join['table'])} rows")
check("join reported in provenance",
      self_join["provenance"]["join"]["rows_matched"] > 0,
      str(self_join["provenance"]["join"]))

print("\n6. Error handling")
for label, kwargs in [
    ("unknown view", {"view": "nope"}),
    ("unknown column", {"view": "client", "group_by": ["Nonexistent"]}),
    ("non-numeric measure", {"view": "client", "group_by": ["Currency"], "measure": "Account Name"}),
]:
    try:
        data_access.run_query(**kwargs)
        check(label + " raises", False, "no error raised")
    except data_access.QueryError as exc:
        check(label + " raises QueryError", True, str(exc)[:60])

print("\n7. Chart rendering")
for ctype, res in [("bar", r1), ("barh", r3)]:
    out = report_builder.build_chart(res["table"], ctype, f"Test {ctype}", OUT, "smoke")
    check(f"{ctype} chart written", out["chart_path"] and out["chart_path"].exists(),
          str(out["chart_path"].name) if out["chart_path"] else "none")

scalar = report_builder.build_chart(r2["table"], "bar", "Unmatched accounts", OUT, "smoke")
check("scalar renders as hero stat", scalar["chart_type"] == "stat")

print("\n8. Exports")
report = {
    "title": "Smoke Test Report",
    "user_query": "test query",
    "understood": "A test interpretation.",
    "narrative": "This is test commentary.",
    "limitations": ["Synthetic data.", "FX rates are static."],
    "dependencies": ["Depends on the value-date filter."],
    "chart_path": out["chart_path"],
    "chart_notes": out["notes"],
    "table": report_builder.format_table_for_display(r3["table"]),
    "provenance": r3["provenance"],
    "history": [{"role": "user", "content": "test query"}],
}
pdf_path = pdf_export.build_pdf(report, OUT, "smoke")
check("PDF written", pdf_path.exists(), f"{pdf_path.stat().st_size:,} bytes")
md_path = md_export.build_markdown(report, OUT, "smoke")
check("MD written", md_path.exists(), f"{md_path.stat().st_size:,} bytes")

md_text = md_path.read_text(encoding="utf-8")
for required in ("Limitations of this view", "Dependencies",
                 "Definitions of the fields used", "Controls in effect",
                 "How this was produced"):
    check(f"MD section '{required}'", required in md_text)

# The real risk is a section rendering empty because a view label did not match.
check("MD dictionary section populated",
      "_No matching data-dictionary entries._" not in md_text)
check("MD controls section populated",
      "_No recorded controls for this view._" not in md_text)
check("MD names the derived-column formula",
      "Formula:" in md_text)
check("MD carries the figures", "Cashflow Type" in md_text)

print("\n" + ("-" * 55))
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All smoke tests passed.")
