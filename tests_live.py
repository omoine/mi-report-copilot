"""Live end-to-end test against the real model. Requires OPENAI_API_KEY.

Makes a handful of real API calls. Prints the assistant's actual output so the
quality of the interpretation, caveats and commentary can be judged by eye.

Run: .venv\\Scripts\\python.exe tests_live.py
"""

import sys

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def rule(text: str) -> None:
    print(f"\n{'=' * 62}\n{text}\n{'=' * 62}")


health = client.get("/api/health").json()
if not health["api_key_configured"]:
    print("OPENAI_API_KEY not set - cannot run live test.")
    sys.exit(1)
print(f"Model: {health['model']}  |  Data: {health['data_file']}")

# ---------------------------------------------------------------- scenario 1
rule("SCENARIO 1  'Which client accounts failed reconciliation today?'")

r = client.post("/api/query", json={"query": "Which client accounts failed reconciliation today?"})
if r.status_code != 200:
    print(f"  FAILED: {r.status_code} {r.text[:300]}")
    sys.exit(1)
d = r.json()
sid = d["session_id"]

print(f"\nUNDERSTOOD:\n  {d['understood']}")
print(f"\nQUERY IT WILL RUN:\n  {d['query_summary']}")
print("\nLIMITATIONS IT RAISED:")
for item in d["limitations"]:
    print(f"  - {item}")
print("\nDEPENDENCIES IT RAISED:")
for item in d["dependencies"]:
    print(f"  - {item}")

check("interpretation returned", bool(d["understood"]))
check("stopped for confirmation", d["state"] == "awaiting_confirmation")
check("raised at least one limitation", len(d["limitations"]) >= 1)
check("picked the client view", "Client View" in d["query_summary"], d["query_summary"][:80])

print("\n--- user confirms ---")
r = client.post("/api/confirm", json={"session_id": sid})
check("report built", r.status_code == 200, f"status {r.status_code}")
if r.status_code != 200:
    print(r.text[:400])
    sys.exit(1)
rep = r.json()
print(f"\nTITLE: {rep['title']}")
print(f"\nCOMMENTARY:\n  {rep['narrative']}")
print(f"\nRESULT ({rep['table']['total_rows']} rows), columns: {rep['table']['columns']}")
for row in rep["table"]["rows"][:6]:
    print(f"  {row}")
p = rep["provenance"]
print(f"\nPROVENANCE: {p['aggregation']} of {p['measure'] or 'row count'} | "
      f"filters={p['filters']} | {p['rows_after_filters']}/{p['rows_in_view']} rows")

check("has figures", len(rep["table"]["rows"]) > 0)
check("commentary written", len(rep["narrative"]) > 20)

# ---------------------------------------------------------------- scenario 2
rule("SCENARIO 2  fine-tuning the report")

r = client.post("/api/refine", json={
    "session_id": sid,
    "instruction": "Show this as a horizontal bar chart of the difference amount by account name",
})
check("refine accepted", r.status_code == 200, f"status {r.status_code}")
if r.status_code == 200:
    ref = r.json()
    print(f"\nAFTER REFINEMENT: {ref['title']}")
    print(f"  grouped by: {ref['provenance']['group_by']}")
    print(f"  measure:    {ref['provenance']['measure']}")
    print(f"  commentary: {ref['narrative'][:180]}")
    check("regrouped as asked", bool(ref["provenance"]["group_by"]))
else:
    print(f"  {r.text[:300]}")

# ---------------------------------------------------------------- scenario 3
rule("SCENARIO 3  export")

r = client.post("/api/export", json={"session_id": sid})
check("export ok", r.status_code == 200, f"status {r.status_code}")
if r.status_code == 200:
    exp = r.json()
    print(f"  PDF: {exp['pdf']}")
    print(f"  MD:  {exp['markdown']}")
    md = client.get(f"/api/download/{exp['markdown']}").text
    check("MD is substantial", len(md) > 3000, f"{len(md):,} chars")
    check("MD documents limitations", "Limitations of this view" in md)
    check("MD documents the query", "The exact query that produced these figures" in md)
    check("MD has field definitions", "Definitions of the fields used" in md)
    check("MD dictionary populated", "_No matching data-dictionary entries._" not in md)
    check("MD controls populated", "_No recorded controls for this view._" not in md)

# ---------------------------------------------------------------- scenario 4
rule("SCENARIO 4  a request the data cannot answer")

r = client.post("/api/query", json={
    "query": "Show me our intraday credit line utilisation against each counterparty limit",
})
print(f"\nHTTP {r.status_code}")
body = r.json()
if r.status_code == 400:
    print(f"DECLINED WITH:\n  {body['detail'][:500]}")
    check("infeasible request declined rather than answered with unrelated data", True)
else:
    print(f"UNDERSTOOD:\n  {body.get('understood')}")
    print(f"QUERY IT WOULD RUN:\n  {body.get('query_summary')}")
    print("LIMITATIONS:")
    for item in body.get("limitations", []):
        print(f"  - {item}")
    # Building *something* here is the dangerous outcome: the reader would get a
    # plausible report that does not answer what they asked.
    check("infeasible request declined rather than answered with unrelated data",
          False, "it accepted the request and would have built a report")

# A second probe: a concept that plainly does not exist in these three views.
r = client.post("/api/query", json={"query": "Show me each counterparty's credit rating"})
print(f"\nSecond probe (credit rating) -> HTTP {r.status_code}")
if r.status_code == 400:
    print(f"  declined: {r.json()['detail'][:200]}")
check("request for a non-existent concept is declined", r.status_code == 400,
      "" if r.status_code == 400 else f"accepted: {r.json().get('query_summary', '')[:90]}")

print("\n" + "-" * 62)
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Live end-to-end test passed.")
