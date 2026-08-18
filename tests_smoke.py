"""Smoke tests: exercise the deterministic layers without needing an API key.

Run: .venv\\Scripts\\python.exe tests_smoke.py
"""

import sys
from pathlib import Path

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
