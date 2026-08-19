"""API tests using a stub LLM provider - no API key or network needed.

Exercises the full flow: query -> confirm -> refine -> export, plus the
validation paths that protect against bad model output.

Run: .venv\\Scripts\\python.exe tests_api.py
"""

import sys

from fastapi.testclient import TestClient

from app import llm_client, main, orchestrator

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class StubProvider:
    """Stands in for the model. Returns fixed, valid structures."""

    def __init__(self) -> None:
        self.json_calls = 0
        self.text_calls = 0
        self.next_json: dict | None = None
        self.queue: list[dict] = []  # consumed in order, for retry scenarios

    def complete_json(self, system, messages):
        self.json_calls += 1
        if self.queue:
            return self.queue.pop(0)
        if self.next_json is not None:
            payload, self.next_json = self.next_json, None
            return payload
        return {
            "understood": "Total nostro transfer value by currency for the loaded value date.",
            "query": {
                "view": "nostro_transfer",
                "filters": [],
                "group_by": ["Currency"],
                "measure": "Value Amount",
                "aggregation": "sum",
                "sort_desc": True,
                "limit": None,
            },
            "chart_type": "bar",
            "limitations": [
                "Amounts are in local currency and are not additive across currencies.",
                "Synthetic, non-production data.",
            ],
            "dependencies": ["Depends on the value-date range selected on the source screen."],
            "feasible": True,
        }

    def complete_text(self, system, messages):
        self.text_calls += 1
        return "JPY accounts for the largest share of transfer value in this view."


stub = StubProvider()
main._provider = stub  # inject before any request builds a real client
client = TestClient(main.app)

print("\n1. Health and schema")
h = client.get("/api/health").json()
check("health ok", h["status"] == "ok", f"views: {h['views']}")
check("data classification exposed", "Synthetic" in h["data_classification"])
s = client.get("/api/schema").json()
check("schema lists 3 views", len(s["views"]) == 3)
check("schema includes controls", len(s["controls"]) > 20)

print("\n2. Query -> interpretation (nothing built yet)")
r = client.post("/api/query", json={"query": "total nostro transfer value by currency"})
check("query accepted", r.status_code == 200, f"status {r.status_code}")
data = r.json()
sid = data["session_id"]
check("state is awaiting_confirmation", data["state"] == "awaiting_confirmation")
check("limitations returned", len(data["limitations"]) == 2)
check("dependencies returned", len(data["dependencies"]) == 1)
check("query summary present", "Value Amount" in data["query_summary"], data["query_summary"][:70])
check("no report yet", "table" not in data)

print("\n3. Confirm -> report built")
r = client.post("/api/confirm", json={"session_id": sid})
check("confirm ok", r.status_code == 200, f"status {r.status_code}")
rep = r.json()
check("state is report_built", rep["state"] == "report_built")
check("chart produced", bool(rep["chart_url"]), rep.get("chart_url"))
check("table has rows", len(rep["table"]["rows"]) > 0, f"{len(rep['table']['rows'])} rows")
check("narrative from model", "JPY" in rep["narrative"])
check("provenance recorded", rep["provenance"]["rows_in_view"] == 37)
check("model was asked for narrative", stub.text_calls == 1)

print("\n4. Chart is served")
r = client.get(rep["chart_url"])
check("chart bytes served", r.status_code == 200 and r.content[:4] == b"\x89PNG",
      f"{len(r.content):,} bytes")

print("\n5. Refine")
stub.next_json = {
    "understood": "Filtered to JPY only.",
    "query": {
        "view": "nostro_transfer",
        "filters": [{"column": "Currency", "operator": "eq", "value": "JPY"}],
        "group_by": ["Sending Strategy"],
        "measure": "Value Amount",
        "aggregation": "sum",
        "sort_desc": True,
        "limit": None,
    },
    "chart_type": "barh",
    "limitations": ["Single currency only."],
    "dependencies": ["Depends on sending strategy being populated."],
    "feasible": True,
}
r = client.post("/api/refine", json={"session_id": sid, "instruction": "just JPY, by sending strategy"})
check("refine ok", r.status_code == 200, f"status {r.status_code}")
ref = r.json()
check("refined filter applied", ref["provenance"]["filters"][0]["value"] == "JPY")
check("fewer rows after filter",
      ref["provenance"]["rows_after_filters"] < ref["provenance"]["rows_in_view"],
      f"{ref['provenance']['rows_after_filters']} of {ref['provenance']['rows_in_view']}")
check("regrouped", ref["provenance"]["group_by"] == ["Sending Strategy"])

print("\n6. Export")
r = client.post("/api/export", json={"session_id": sid})
check("export ok", r.status_code == 200, f"status {r.status_code}")
exp = r.json()
check("pdf named", exp["pdf"].endswith(".pdf"), exp["pdf"])
check("md named", exp["markdown"].endswith(".md"), exp["markdown"])
check("pdf and md are a matching pair",
      exp["pdf"][:-4] == exp["markdown"][:-3], f"{exp['pdf']} / {exp['markdown']}")

r = client.get(f"/api/download/{exp['pdf']}")
check("pdf downloads", r.status_code == 200 and r.content[:4] == b"%PDF",
      f"{len(r.content):,} bytes")

# A short result table must not be orphaned across a page break.
try:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(r.content))
    page1 = reader.pages[0].extract_text() or ""
    n_rows = len(ref["table"]["rows"])
    on_page1 = sum(1 for row in ref["table"]["rows"] if str(row[0]) in page1)
    check("short table stays on one page", on_page1 == n_rows,
          f"{on_page1}/{n_rows} rows on page 1")
except ImportError:
    print("  [SKIP] page-break check (pypdf not installed)")
r = client.get(f"/api/download/{exp['markdown']}")
md = r.text
check("md downloads", r.status_code == 200, f"{len(md):,} chars")
check("md records the refinement history", "just JPY" in md)
check("md records the filter", "JPY" in md)
check("md states figures are deterministic", "deterministic" in md.lower())
check("md has field definitions", "Sending Strategy" in md)

print("\n6b. Export formats")
check("excel produced", bool(exp.get("excel")), exp.get("excel"))
check("svg produced", bool(exp.get("svg")), exp.get("svg"))

r = client.get(f"/api/download/{exp['excel']}")
check("excel downloads", r.status_code == 200 and r.content[:2] == b"PK",
      f"{len(r.content):,} bytes")
try:
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(r.content))
    check("excel has data and about sheets",
          {"Data", "About this view"} <= set(wb.sheetnames), str(wb.sheetnames))
    # An editable chart object is the point of the Excel export.
    check("excel carries a native chart", len(wb["Data"]._charts) > 0,
          f"{len(wb['Data']._charts)} chart(s)")
    about = "\n".join(str(c.value) for row in wb["About this view"].iter_rows()
                      for c in row if c.value)
    check("about sheet records provenance", "deterministic" in about.lower())
except ImportError:
    print("  [SKIP] excel internals (openpyxl missing)")

r = client.get(f"/api/download/{exp['svg']}")
check("svg downloads and is vector", r.status_code == 200 and b"<svg" in r.content[:600],
      f"{len(r.content):,} bytes")

print("\n6c. Saved views")
r = client.post("/api/views/save", json={"session_id": sid, "name": "Eval test view"})
check("view saved", r.status_code == 200, r.json().get("detail", "")[:60])

r = client.get("/api/views")
names = [v["name"] for v in r.json()["views"]]
check("view appears in the list", "Eval test view" in names, str(names[:4]))

r = client.get("/api/views?search=eval")
check("search finds it", any(v["name"] == "Eval test view" for v in r.json()["views"]))
r = client.get("/api/views?search=zzzznotathing")
check("search excludes non-matches", r.json()["views"] == [])

# Saving the same name again must ask rather than silently overwrite.
r = client.post("/api/views/save", json={"session_id": sid, "name": "Eval test view"})
check("duplicate name is refused", r.status_code == 400,
      r.json().get("detail", "")[:60])
r = client.post("/api/views/save",
                json={"session_id": sid, "name": "Eval test view", "overwrite": True})
check("overwrite works when confirmed", r.status_code == 200)

view_id = [v["id"] for v in client.get("/api/views").json()["views"]
           if v["name"] == "Eval test view"][0]
r = client.post("/api/views/load", json={"view_id": view_id})
check("saved view reloads and re-runs", r.status_code == 200,
      r.json().get("detail", "")[:80])
if r.status_code == 200:
    reloaded = r.json()
    check("reload returns figures", len(reloaded["table"]["rows"]) > 0)
    check("reload names the view", reloaded.get("loaded_view", {}).get("name") == "Eval test view")

r = client.delete(f"/api/views/{view_id}")
check("view deleted", r.status_code == 200)
check("deleted view is gone",
      "Eval test view" not in [v["name"] for v in client.get("/api/views").json()["views"]])
r = client.delete(f"/api/views/{view_id}")
check("deleting twice is a clean 404", r.status_code == 404)

print("\n7. Guard rails")
r = client.post("/api/export", json={"session_id": "nonexistent-session"})
check("export without report is rejected", r.status_code == 400, r.json().get("detail", "")[:50])

r = client.post("/api/refine", json={"session_id": "another-new-session", "instruction": "x"})
check("refine before build is rejected", r.status_code == 400)

r = client.get("/api/download/../../etc/passwd")
check("path traversal blocked", r.status_code in (403, 404), f"status {r.status_code}")

stub.next_json = {"understood": "Cannot do this.", "feasible": False,
                  "limitations": ["No FX forward data exists in these views."], "query": {}}
r = client.post("/api/query", json={"query": "show me FX forwards"})
check("infeasible request rejected cleanly", r.status_code == 400,
      r.json().get("detail", "")[:60])
check("decline explains why, not just what was asked",
      "No FX forward data" in r.json().get("detail", ""),
      r.json().get("detail", "")[:90])

stub.next_json = {"understood": "x", "feasible": True,
                  "query": {"view": "not_a_view"}, "limitations": [], "chart_type": "bar"}
r = client.post("/api/query", json={"query": "something"})
check("bad view from model rejected", r.status_code == 400)

bad_column = {"understood": "x", "feasible": True, "limitations": [], "chart_type": "bar",
              "query": {"view": "client", "group_by": ["No Such Column"],
                        "measure": "Calculated Balance (Local)", "aggregation": "sum"}}
good_column = {"understood": "x", "feasible": True, "limitations": [], "chart_type": "bar",
               "query": {"view": "client", "group_by": ["Currency"],
                         "measure": "Calculated Balance (Local)", "aggregation": "sum"}}

# A wrong column should be retried once with the error fed back to the model.
stub.queue = [bad_column, good_column]
before = stub.json_calls
r = client.post("/api/query", json={"query": "recoverable mistake"})
check("bad column is retried and recovers", r.status_code == 200, f"status {r.status_code}")
check("retry made exactly one extra model call", stub.json_calls - before == 2,
      f"{stub.json_calls - before} calls")

# Failing twice must surface to the user rather than loop.
stub.queue = [bad_column, bad_column]
r = client.post("/api/query", json={"query": "unrecoverable mistake"})
check("bad column fails after one retry", r.status_code == 400,
      r.json().get("detail", "")[:60])
stub.queue = []

r = client.post("/api/query", json={"query": "   "})
check("empty query rejected", r.status_code == 400)

print("\n8. Narrative failure does not lose the report")


class FailingNarrative(StubProvider):
    def complete_text(self, system, messages):
        raise llm_client.LLMError("simulated model outage")


main._provider = FailingNarrative()
r = client.post("/api/query", json={"query": "total nostro transfer value by currency"})
sid2 = r.json()["session_id"]
r = client.post("/api/confirm", json={"session_id": sid2})
check("report still built without narrative", r.status_code == 200)
check("figures still present", len(r.json()["table"]["rows"]) > 0)
check("fallback commentary shown", "could not be generated" in r.json()["narrative"].lower())

print("\n" + "-" * 55)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All API tests passed.")
