"""View review harness.

Runs a defined set of executive-level MI questions end to end and writes a
transcript of exactly what the tool produced for each: the interpretation, the
query, the caveats, the shape of the result and the first rows.

The point is not pass/fail. It is to put the actual output in front of a reviewer
so the gap between "the query ran" and "a manager could act on this" is visible.

Run: .venv\\Scripts\\python.exe tests_views.py [--only 3] [--out exports/view_review.md]
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)

# (number, name, what an executive is actually asking, the prompt)
VIEWS: list[tuple[int, str, str, str]] = [
    (1, "Intraday peak liquidity usage",
     "What is the largest intraday position we ran, and in which currency? "
     "This is the core BCBS 248 monitoring metric.",
     "What was our largest intraday liquidity usage by currency? Show the "
     "cumulative net position through the day and where it peaked."),

    (2, "Reconciliation exceptions",
     "Which balances did not reconcile, how big are the breaks, and which need "
     "action today?",
     "Show me all client accounts that failed reconciliation, with the size of "
     "the difference, ordered by the largest break first."),

    (3, "Payment failures and rejections",
     "What failed, what value was at risk, and is it concentrated anywhere?",
     "How many transfers failed or were rejected, what was their total value, "
     "and which currencies and venues were affected?"),

    (4, "Currency concentration",
     "Where is our liquidity concentrated, and is any single currency an "
     "outsized share of the total?",
     "Show total transfer value by currency with each currency's share of the "
     "total, largest first."),

    (5, "Counterparty concentration",
     "Which counterparties carry most of our flow, and how concentrated is that?",
     "Which counterparties account for the largest share of ledger flow? Show "
     "the top ten with their share of the total."),

    (6, "Intraday flow timing",
     "When does money arrive and leave? Are we funding early enough?",
     "Show the intraday profile of ledger flows by hour, separating debits from "
     "credits, so I can see when money arrives versus when it leaves."),

    (7, "Approval pipeline",
     "What is waiting on a human, and how long has it been waiting?",
     "What is sitting in the approval queue, and how long has it been waiting? "
     "I want to see the value at risk in each ageing band."),

    (8, "Entity and desk performance",
     "How does activity break down across legal entities and desks?",
     "Show total ledger flow by sub branch and legal entity so I can compare "
     "desks."),

    (9, "Day-over-day trend",
     "Is today normal? How does it compare with the rest of the month?",
     "Show total daily transfer value across the month so I can see which days "
     "were unusual."),

    (10, "Largest individual movements",
     "Which single transactions are big enough that I should know about them?",
     "List the twenty largest ledger transactions this month with counterparty, "
     "account, currency and amount."),
]


def run_view(number: int, name: str, intent: str, prompt: str) -> dict:
    record = {"number": number, "name": name, "intent": intent, "prompt": prompt}

    r = client.post("/api/query", json={"query": prompt})
    if r.status_code != 200:
        record["outcome"] = "declined"
        record["detail"] = r.json().get("detail", "")
        return record

    interp = r.json()
    record["understood"] = interp.get("understood", "")
    record["query_summary"] = interp.get("query_summary", "")
    record["limitations"] = interp.get("limitations", [])
    record["dependencies"] = interp.get("dependencies", [])

    b = client.post("/api/confirm", json={"session_id": interp["session_id"]})
    if b.status_code != 200:
        record["outcome"] = "build_failed"
        record["detail"] = b.json().get("detail", "")
        return record

    rep = b.json()
    record["outcome"] = "built"
    record["title"] = rep.get("title", "")
    record["chart_type"] = rep.get("chart_type", "")
    record["narrative"] = rep.get("narrative", "")
    record["columns"] = rep["table"]["columns"]
    record["rows"] = rep["table"]["rows"][:8]
    record["total_rows"] = rep["table"]["total_rows"]
    record["provenance"] = rep.get("provenance", {})
    record["chart_notes"] = rep.get("chart_notes", [])
    return record


def to_markdown(records: list[dict]) -> str:
    out = [
        "# View review",
        "",
        f"_Generated {dt.datetime.now():%d %B %Y %H:%M}. "
        "What the tool actually produced for each question, as the basis for "
        "deciding what each view should become._",
        "",
    ]
    for r in records:
        out += [f"## {r['number']}. {r['name']}", "",
                f"**What is being asked:** {r['intent']}", "",
                f"**Prompt:** _{r['prompt']}_", ""]

        if r["outcome"] != "built":
            out += [f"**Outcome: {r['outcome'].upper()}**", "",
                    f"> {r.get('detail', '')[:600]}", "", "---", ""]
            continue

        out += [f"**Understood as:** {r['understood']}", "",
                f"**Query run:** {r['query_summary']}", "",
                f"**Result:** {r['total_rows']} rows, "
                f"presented as {r['chart_type'] or 'a table'}", "",
                "| " + " | ".join(str(c) for c in r["columns"]) + " |",
                "|" + "|".join("---" for _ in r["columns"]) + "|"]
        for row in r["rows"]:
            out.append("| " + " | ".join(str(v) for v in row) + " |")
        if r["total_rows"] > len(r["rows"]):
            out.append("")
            out.append(f"_showing {len(r['rows'])} of {r['total_rows']} rows_")

        out += ["", f"**Commentary:** {r['narrative']}", ""]
        if r["limitations"]:
            out += ["**Limitations stated:**", ""]
            out += [f"- {x}" for x in r["limitations"]]
            out.append("")
        if r["chart_notes"]:
            out += ["**Chart notes:**", ""]
            out += [f"- {x}" for x in r["chart_notes"]]
            out.append("")
        out += ["---", ""]
    return "\n".join(out)


def main_run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, help="run a single view by number")
    parser.add_argument("--out", type=Path,
                        default=Path("exports") / "view_review.md")
    args = parser.parse_args()

    health = client.get("/api/health").json()
    if not health["api_key_configured"]:
        print("No usable API key configured.")
        return 1
    print(f"Data: {health['data_file']}  {health['views']}\n")

    todo = [v for v in VIEWS if not args.only or v[0] == args.only]
    records = []
    for number, name, intent, prompt in todo:
        print(f"[{number:>2}] {name}")
        try:
            record = run_view(number, name, intent, prompt)
        except Exception as exc:
            record = {"number": number, "name": name, "intent": intent,
                      "prompt": prompt, "outcome": "exception",
                      "detail": f"{type(exc).__name__}: {exc}"}
        records.append(record)
        mark = {"built": "  ok  ", "declined": " DECL ",
                "build_failed": " FAIL ", "exception": " EXC  "}.get(record["outcome"], "  ?   ")
        print(f"     [{mark}] {record.get('query_summary', record.get('detail', ''))[:140]}")
        if record["outcome"] == "built":
            print(f"            -> {record['total_rows']} rows, "
                  f"columns {record['columns']}")
        print()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_markdown(records), encoding="utf-8")
    built = sum(1 for r in records if r["outcome"] == "built")
    print(f"{built}/{len(records)} produced a report.  Transcript -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_run())
