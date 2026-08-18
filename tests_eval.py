"""Evaluation harness: how many realistic MI questions actually get answered well?

Runs a fixed question set through the live model and records, for each one,
whether it was interpreted, what query it produced, whether it built, and what
the answer actually looked like. Prints a scoreboard by category.

This is a quality measurement, not a pass/fail test - read the output.

Run: .venv\\Scripts\\python.exe tests_eval.py [category]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)

# (category, question, expectation)
#   build   - should produce a useful report
#   decline - should be declined as unsupported
QUESTIONS: list[tuple[str, str, str]] = [
    # --- plain aggregation: the case that already worked -----------------
    ("aggregate", "Total nostro transfer value by currency", "build"),
    ("aggregate", "Business ledger amounts by cashflow type", "build"),
    ("aggregate", "What is the total value of all nostro transfers?", "build"),
    ("aggregate", "Average client account balance by legal entity", "build"),

    # --- detail listings: "show me the actual rows" ----------------------
    ("listing", "Which client accounts failed reconciliation, and by how much?", "build"),
    ("listing", "List the nostro transfers still pending approval", "build"),
    ("listing", "Show me the ten largest business ledger transactions", "build"),
    ("listing", "Which transfers were rejected by the message channel?", "build"),

    # --- intraday time profile: the core of liquidity monitoring ---------
    ("time", "Show me nostro transfer volume by hour of the day", "build"),
    ("time", "What does the intraday profile of ledger transactions look like?", "build"),
    ("time", "How many transfers were created each hour this morning?", "build"),

    # --- comparison / two measures at once -------------------------------
    ("compare", "Compare credits and debits by currency for client accounts", "build"),
    ("compare", "Show start of day balance against calculated balance per account", "build"),
    ("compare", "Debit versus credit totals by sub branch", "build"),

    # --- ranking ---------------------------------------------------------
    ("ranking", "Top 5 counterparties by ledger amount", "build"),
    ("ranking", "Which three currencies have the largest transfer value?", "build"),

    # --- filtered --------------------------------------------------------
    ("filtered", "Total JPY transfer value sent via SWIFT", "build"),
    ("filtered", "How many transfers failed?", "build"),
    ("filtered", "Show ledger transactions over 10 million in display currency", "build"),
    ("filtered", "Client accounts where the reconciliation difference is not zero", "build"),

    # --- should be declined ---------------------------------------------
    ("decline", "Show me intraday credit line utilisation against counterparty limits", "decline"),
    ("decline", "What is each counterparty's credit rating?", "decline"),
    ("decline", "Forecast tomorrow's liquidity requirement", "decline"),
]


def evaluate(question: str, expectation: str) -> dict:
    """Run one question end to end and describe what happened."""
    result: dict = {"question": question, "expected": expectation}

    r = client.post("/api/query", json={"query": question})
    if r.status_code == 400:
        result["outcome"] = "declined"
        result["detail"] = r.json().get("detail", "")[:220]
        result["ok"] = expectation == "decline"
        return result
    if r.status_code != 200:
        result["outcome"] = f"error_{r.status_code}"
        result["detail"] = r.text[:220]
        result["ok"] = False
        return result

    interp = r.json()
    sid = interp["session_id"]
    result["understood"] = interp.get("understood", "")
    result["query_summary"] = interp.get("query_summary", "")
    result["n_limitations"] = len(interp.get("limitations", []))

    b = client.post("/api/confirm", json={"session_id": sid})
    if b.status_code != 200:
        result["outcome"] = "build_failed"
        result["detail"] = b.json().get("detail", "")[:220]
        result["ok"] = False
        return result

    rep = b.json()
    rows = rep["table"]["rows"]
    cols = rep["table"]["columns"]
    result["outcome"] = "built"
    result["rows"] = len(rows)
    result["columns"] = cols
    result["chart_type"] = rep.get("chart_type")
    result["sample"] = rows[:3]
    result["narrative"] = (rep.get("narrative") or "")[:150]

    # Quality gates beyond "it returned 200".
    summary = (result.get("query_summary") or "").lower()
    mode = rep.get("provenance", {}).get("mode", "aggregate")
    result["mode"] = mode
    problems = []

    if expectation == "decline":
        problems.append("should have been declined but built a report")
    if not rows:
        problems.append("empty result")

    # A count that is 1 for every group means the grouping answered nothing.
    if len(cols) == 2 and cols[-1] == "count" and rows:
        if {row[-1] for row in rows} == {"1.00"}:
            problems.append("degenerate count (1 per group) - should be a listing")

    # Category-specific expectations, which is where the real quality lives.
    if category_of(question) == "listing":
        if mode != "list":
            problems.append(f"asked to see records but ran an {mode} query")
        elif len(cols) < 3:
            problems.append(f"listing shows only {len(cols)} column(s) - too thin to read")
    if category_of(question) == "time":
        if "bucketed by" not in summary:
            problems.append("time question did not bucket the timestamp")
        elif len(rows) > 30:
            problems.append(f"{len(rows)} time buckets - too granular to read as a profile")
    if category_of(question) == "compare":
        # Two valid shapes: two measure columns, or one measure split by a
        # categorical (which the engine pivots into two columns).
        series_shown = max(0, len(cols) - 1)
        if series_shown < 2:
            problems.append(
                f"comparison shows {series_shown} series - needs two things side by side")
    if category_of(question) == "ranking" and not rep.get("provenance", {}).get("limit"):
        if len(rows) > 10:
            problems.append("ranking question returned everything, no top-N limit")

    result["problems"] = problems
    result["ok"] = not problems
    return result


_CATEGORY_BY_QUESTION = {q: c for c, q, _ in QUESTIONS}


def category_of(question: str) -> str:
    return _CATEGORY_BY_QUESTION.get(question, "")


def main_run() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    questions = [q for q in QUESTIONS if not only or q[0] == only]

    health = client.get("/api/health").json()
    if not health["api_key_configured"]:
        print("No usable API key configured.")
        sys.exit(1)
    print(f"Model: {health['model']}   Questions: {len(questions)}\n")

    results = []
    for category, question, expectation in questions:
        print(f"[{category}] {question}")
        try:
            r = evaluate(question, expectation)
        except Exception as exc:  # keep the run going
            r = {"question": question, "expected": expectation, "outcome": "exception",
                 "detail": f"{type(exc).__name__}: {exc}", "ok": False, "problems": ["exception"]}
        r["category"] = category
        results.append(r)

        mark = "OK  " if r["ok"] else "BAD "
        print(f"   {mark} {r['outcome']}", end="")
        if r.get("rows") is not None:
            print(f"  rows={r['rows']} cols={r.get('columns')}", end="")
        print()
        if r.get("query_summary"):
            print(f"        -> {r['query_summary'][:150]}")
        for p in r.get("problems", []):
            print(f"        !! {p}")
        if r.get("detail"):
            print(f"        :: {r['detail'][:150]}")
        print()

    # Scoreboard
    print("=" * 66)
    print("SCOREBOARD")
    print("=" * 66)
    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    total_ok = 0
    for cat, items in by_cat.items():
        ok = sum(1 for i in items if i["ok"])
        total_ok += ok
        bar = "#" * ok + "." * (len(items) - ok)
        print(f"  {cat:<10} {ok}/{len(items)}  {bar}")
    print(f"\n  TOTAL      {total_ok}/{len(results)}  "
          f"({100 * total_ok / len(results):.0f}%)")

    out = Path(__file__).parent / "exports" / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n  detail written to {out.name}")


if __name__ == "__main__":
    main_run()
