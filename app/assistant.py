"""The in-app assistant.

Two jobs, depending on where the user is:

- **Before a report exists** it helps shape the request, so a question that will
  not produce the intended view is caught before it is run rather than after.
- **Once a report exists** it explains what is on screen.

The second job is why this module is careful about grounding. The exported
Markdown was already designed to let a reader interrogate a report with an AI;
if the in-app assistant were given its own summary instead, the two could
describe the same figures differently and the tool would contradict its own
export. So the assistant is handed *exactly* the string that gets exported -
`md_export.render_markdown` - and nothing else about the report.
"""

from __future__ import annotations

import json
from typing import Any

from . import data_access, llm_client, md_export, prompts

MAX_HISTORY = 12  # turns kept; the report context is resent every time


DESIGN_RULES = """
You are helping someone design a management information view before it is built.
You know exactly what this tool can and cannot do, and what is in the data.

Your job is to make sure the question they ask will actually produce the view
they have in mind. Be concrete and brief - two or three sentences unless they
ask for more.

- If their idea is well supported, say so and give them the exact wording to
  paste into the ask box. Put it on its own line prefixed with `TRY:` so the
  interface can offer it as a button.
- If it will not produce what they expect, say why and offer the closest thing
  that will. The commonest traps: asking for a total across currencies where the
  amounts are in different currencies; asking for a breakdown that would run to
  hundreds of rows; asking for something the data does not carry at all.
- If the data cannot support it, say so plainly and name the missing field. Do
  not invent a workaround that answers a different question.
- Never state a figure. You have not run anything. If they ask what the number
  is, tell them to build the view.
""".strip()

EXPLAIN_RULES = """
You are answering questions about a management information report the user is
looking at right now.

The document below is the report's full supporting record, and it is the same
document the user can export. Answer ONLY from it.

- Every figure you give must appear in that document. Never estimate, never
  recompute, never round in a way that changes a value.
- If the answer is not in the document, say so and say what would be needed.
  Guessing is worse than not answering here.
- Carry the limitations into any answer that uses the figures. If a number is
  caveated in section 4, the caveat travels with it.
- If the user asks about something in "what this view cannot tell you", explain
  that it is absent from the data and what would have to be captured.
- Be brief and concrete. Two to four sentences unless asked for more.
- The user can see the report; do not restate it back to them wholesale.
""".strip()


def _design_context() -> str:
    """Schema and capabilities, so advice is grounded in what actually exists."""
    schema = json.dumps(data_access.schema_summary(), indent=2, default=str)
    meta = data_access.get_metadata()
    return f"""
DATA CLASSIFICATION: {meta['data_classification']}.

AVAILABLE VIEWS AND COLUMNS:
{schema}

REFERENCE TABLES - attributes that can be joined onto the transaction data.
Each has a single key column, so a lookup only needs the column on the
transaction side. An attribute listed here IS available, through a join:
{prompts._reference_block()}

WHAT THE TOOL CAN DO:
- totals, averages, counts and percentiles, broken down by any column
- listings of matching records, sorted and limited
- distributions: mean, median, quantiles, spread, box plots and histograms
- peak intraday position: the running net position within each day, its high and
  low and when they occurred
- time series by minute, hour, day, week or month, with a period average and
  unusual periods marked
- repeating time parts: hour of the day, day of the week, day of the month
- durations between two timestamps, and ageing bands for a queue
- rates: the share of records in a group meeting a condition
- share of total, running totals, and calculated columns
- data completeness: how populated every column is
- combining two views, where they share a key

WHAT IT WILL REFUSE, AND WHY:
- adding amounts that are in different currencies, unless an FX-translated
  column exists, because the total would combine unlike units
- subtracting two timestamps arithmetically, which yields nanoseconds
- breaking a date with no time of day down by hour
- answering a question about a concept the data does not carry

KNOWN GAPS IN THIS DATA:
- the transfer view has no FX-translated amount, so cross-currency totals of
  transfer value are refused
- no reason is recorded for a failed or rejected transfer
- legal entity is on the client view only and cannot be attributed to ledger
  postings
- the upstream reference linking ledger postings to transfers is populated on
  under half of rows
- accounts with no activity do not appear at all, so the dormant estate cannot
  be sized
""".strip()


def _has_report(session: Any) -> bool:
    return bool(getattr(session, "report", None))


def build_system_prompt(session: Any, message: str = "") -> str:
    """Design guidance before a report exists, grounded explanation after.

    Both modes are told where the values named in the question actually live.
    Without it the assistant answers "there is no such field" beside a tool that
    is about to answer the same question correctly, and the reader has no way to
    tell which of the two is right.
    """
    resolved = prompts._resolved_values_block(message)

    if not _has_report(session):
        parts = [DESIGN_RULES, _design_context()]
    else:
        report = dict(session.report)
        report["history"] = session.history
        document = md_export.render_markdown(report)
        parts = [
            EXPLAIN_RULES,
            "--- THE REPORT'S SUPPORTING DOCUMENT (the export, verbatim) ---\n"
            + document,
            # Figures still come from the document above. This is here only so a
            # question about what ELSE could be built is not answered with a
            # denial of data the tool holds.
            "--- WHAT ELSE THE DATA MODEL OFFERS (not figures - availability "
            "only) ---\n" + prompts._reference_block(),
        ]

    if resolved:
        parts.append(resolved)
    return "\n\n".join(parts)


def answer(session: Any, message: str, history: list[dict[str, str]],
           provider: llm_client.LLMProvider) -> dict[str, Any]:
    """One turn of conversation about the current state."""
    if not message.strip():
        raise ValueError("Ask a question first.")

    messages = []
    for turn in (history or [])[-MAX_HISTORY:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message.strip()})

    reply = provider.complete_text(
        build_system_prompt(session, message), messages)

    # A suggested prompt is offered as a button rather than left for the user to
    # retype, since retyping is where the intent gets lost.
    suggestion = None
    lines = []
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TRY:"):
            suggestion = stripped[4:].strip().strip('"').strip()
        else:
            lines.append(line)
    cleaned = "\n".join(lines).strip() or reply.strip()

    return {
        "reply": cleaned,
        "suggestion": suggestion,
        "mode": "explain" if _has_report(session) else "design",
    }
