"""Install the reviewed views as named, recurring saved views.

Only the views judged fit to put in front of an executive are here. Each is run
through the tool and stored under a business name, so a user picks it from the
list rather than rewriting the question and hoping for the same interpretation.

A saved view stores the specification, not the results, so each of these is a
live question that re-runs against whatever the data currently holds.

Run: .venv\\Scripts\\python.exe seed_views.py [--replace]
"""

from __future__ import annotations

import argparse
import sys

from fastapi.testclient import TestClient

from app import auth, main

client = TestClient(main.app)

# The application is behind a password, so the suite signs in the way a user
# does. Testing against an unlocked app would leave the gate itself untested
# and hide the day it starts rejecting everything.
client.post("/api/login", json={"password": auth.password()})

# (name, description, prompt)
#
# The last three ask for something the data cannot supply - a failure reason, a
# legal entity on the ledger. They are included anyway: the view answers what it
# can and declares the rest under "what this view cannot tell you", so the gap
# is visible in the output rather than hidden by a narrower question.
VIEWS: list[tuple[str, str, str]] = [
    ("Peak intraday liquidity usage",
     "Largest intraday position per currency per day, worst first, with the "
     "time it peaked and where the day closed. The BCBS 248 monitoring shape.",
     "What was our peak intraday liquidity usage by currency? Show the "
     "cumulative net position through the day and where it peaked."),

    ("Currency concentration",
     "Transfer value by currency with each currency's share of the GBP-equivalent "
     "total, largest first.",
     "Show total transfer value by currency with each currency's share of the "
     "total, largest first."),

    ("Top counterparty concentration",
     "The ten counterparties carrying most ledger flow, with their share of the "
     "total.",
     "Which counterparties account for the largest share of ledger flow? Show "
     "the top ten with their share of the total."),

    ("Intraday flow timing",
     "Credits against debits by hour for the most recent day, showing when money "
     "arrives versus when it leaves.",
     "Show the intraday profile of ledger flows by hour, separating debits from "
     "credits, so I can see when money arrives versus when it leaves."),

    ("Approval queue by ageing",
     "Value awaiting approval, banded by how long it has been waiting. A snapshot of how bad the queue is now.",
     "What is sitting in the approval queue, and how long has it been waiting? "
     "I want to see the value at risk in each ageing band."),

    ("Approval queue through the day",
     "How the queue built and drained, every 15 minutes: arrivals, clearances and what was still outstanding. The ageing view says how bad it is; this says how it got that way.",
     "Show how the approval queue evolved through 18 August in 15 minute "
     "intervals - what arrived, what cleared and what was still outstanding."),

    ("Daily flow trend",
     "Daily transfer value across the period against the period average, with "
     "unusual days marked.",
     "Show total daily transfer value across the month so I can see which days "
     "were unusual."),

    ("Largest individual movements",
     "The twenty largest ledger transactions with counterparty, account and "
     "both local and display amounts.",
     "List the twenty largest ledger transactions this month with counterparty, "
     "account, currency and amount."),

    ("Reconciliation breaks by account",
     "Accounts with reconciliation breaks, showing how many days each broke and "
     "the total difference, so repeat offenders are visible rather than every "
     "individual break.",
     "Which client accounts had reconciliation breaks? For each one show how "
     "many days it broke and the total difference, worst first, so I can see "
     "the repeat offenders rather than every individual break."),

    ("Payment failures and rejections",
     "Failed and rejected transfers by status, with count and value at risk. "
     "States that the failure reason is not captured anywhere in the data.",
     "Break down transfers that failed or were rejected by their status, with "
     "the count and total value in each, and tell me why they failed."),

    ("Desk performance",
     "Ledger flow by desk with each desk's share. States that legal entity "
     "cannot be attributed to the ledger view.",
     "Compare our desks: total ledger flow by sub branch, with each desk's "
     "share, and break it down by legal entity as well."),

    # ---- the second ten ------------------------------------------------
    # Account dormancy is not here: it can only return a single count, because
    # the engine cannot compute a filtered and an unfiltered aggregate side by
    # side, and dormant accounts are absent from the extract entirely.
    ("Funding efficiency",
     "Credits against debits by hour for the most recent day, with a running "
     "total, showing whether inflows arrived before outflows.",
     "For the most recent day, show credits and debits by hour with the running "
     "net position, so I can see whether inflows arrived before outflows."),

    ("Cut-off risk",
     "Value settling after 16:00, by currency and desk. Late concentration is "
     "where an operational delay becomes a liquidity problem.",
     "How much ledger value settles after 16:00 each day, and which currencies "
     "and desks does it sit in?"),

    ("Approval turnaround",
     "Median and 95th percentile time from creation to approval. The queue view "
     "says what is waiting; this says how long waiting normally lasts.",
     "How long does it take for transfers to go from created to approved? Show "
     "the median and 95th percentile turnaround by desk."),

    ("Reconciliation break trend",
     "Breaks per day across the period against the average, with unusual days "
     "marked, so it is clear whether breaks are growing or being cleared.",
     "Show the number of reconciliation breaks per day across the month, in date "
     "order, so I can see whether breaks are growing or being cleared."),

    ("Currency net position",
     "Net position by currency across the period - which currencies we are "
     "structurally short of and long in.",
     "Show the net position by currency across the month - credits less debits - "
     "so I can see which currencies we are structurally short of."),

    ("Venue reliability",
     "Failure and rejection rate by venue, as a proportion of what we send them. "
     "A rate, not a count - three failures in five beats ten in a thousand.",
     "Which venues have the highest rate of failed or rejected transfers, "
     "relative to the total volume we send them?"),

    ("Balance volatility by account",
     "Accounts ranked by how much their daily balance swings. Volatile balances "
     "are where buffers are set and forecasting is hardest.",
     "Which client accounts have the most volatile daily balance swing? Show the "
     "spread of their daily swing, largest first."),

    ("Weekday flow pattern",
     "Ledger flow by day of the week, to see which days are consistently heavier.",
     "Compare total daily ledger flow by day of the week, so I can see which "
     "days are consistently heavier."),

    ("Data completeness",
     "How populated every column of the ledger is, least complete first. Every "
     "other view inherits the quality of these fields.",
     "How complete is the business ledger data? Show every column with how many "
     "rows are populated and how many are missing, least complete first."),
]


def seed(replace: bool) -> int:
    health = client.get("/api/health").json()
    if not health["api_key_configured"]:
        print("No usable API key configured.")
        return 1
    print(f"Data: {health['data_file']}\n")

    saved, failed = 0, 0
    for name, description, prompt in VIEWS:
        print(f"  {name}")
        r = client.post("/api/query", json={"query": prompt})
        if r.status_code != 200:
            print(f"    SKIPPED - {r.json().get('detail', '')[:120]}")
            failed += 1
            continue
        sid = r.json()["session_id"]

        b = client.post("/api/confirm", json={"session_id": sid})
        if b.status_code != 200:
            print(f"    SKIPPED - build failed: {b.json().get('detail', '')[:120]}")
            failed += 1
            continue
        rows = b.json()["table"]["total_rows"]

        s = client.post("/api/views/save", json={
            "session_id": sid, "name": name,
            "description": description, "overwrite": replace,
        })
        if s.status_code != 200:
            print(f"    NOT SAVED - {s.json().get('detail', '')[:120]}")
            failed += 1
            continue
        print(f"    saved ({rows} rows)")
        saved += 1

    print(f"\n{saved} saved, {failed} skipped.")
    listing = client.get("/api/views").json()["views"]
    print(f"\nAvailable to users ({len(listing)}):")
    for v in listing:
        print(f"  - {v['name']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", action="store_true",
                        help="overwrite views that already exist")
    args = parser.parse_args()
    sys.exit(seed(args.replace))
