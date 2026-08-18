"""Conversation state machine.

    AWAITING_QUERY -> AWAITING_CONFIRMATION -> REPORT_BUILT -> (refine loop) -> EXPORTED

The confirmation gate is the point of the design: the assistant states what it
understood and what the limitations are, and nothing is built until the user
agrees. The model chooses the query; this module executes it deterministically.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path
from typing import Any

from . import data_access, llm_client, md_export, pdf_export, prompts, report_builder

# Overridable so a hosted deployment can write to a writable volume (e.g. /tmp)
# when the application directory is read-only.
EXPORT_DIR = Path(
    os.getenv("EXPORT_DIR") or Path(__file__).resolve().parent.parent / "exports"
)

AWAITING_QUERY = "awaiting_query"
AWAITING_CONFIRMATION = "awaiting_confirmation"
REPORT_BUILT = "report_built"

VALID_CHART_TYPES = {"bar", "barh", "line", "table"}


class OrchestratorError(RuntimeError):
    """A user-facing failure (bad request, infeasible query, model problem)."""


class Session:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.state = AWAITING_QUERY
        self.created_at = dt.datetime.now()
        self.user_query: str = ""
        self.interpretation: dict[str, Any] = {}
        self.report: dict[str, Any] = {}
        self.history: list[dict[str, str]] = []

    def log(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content,
                             "at": dt.datetime.now().isoformat(timespec="seconds")})


_SESSIONS: dict[str, Session] = {}


def get_session(session_id: str | None) -> Session:
    """Fetch or create a session. In-memory only - a POC has no persistence."""
    if session_id and session_id in _SESSIONS:
        return _SESSIONS[session_id]
    new_id = session_id or uuid.uuid4().hex[:12]
    _SESSIONS[new_id] = Session(new_id)
    return _SESSIONS[new_id]


def reset_session(session_id: str) -> Session:
    _SESSIONS.pop(session_id, None)
    return get_session(session_id)


def _validate_interpretation(payload: dict[str, Any]) -> dict[str, Any]:
    """Check the model's output before acting on it. Never trust it blindly."""
    if not payload.get("feasible", True):
        # Always give the reason. Restating the request without saying what is
        # missing leaves the user with a bare "no" and nothing to act on.
        reasons = [str(x) for x in payload.get("limitations", []) if x]
        message = payload.get("understood") or "This request cannot be answered from this data."
        if reasons:
            message = f"{message.rstrip('.')}. This cannot be built from the available data: " \
                      + " ".join(reasons)
        raise OrchestratorError(message)

    query = payload.get("query") or {}
    view = query.get("view")
    if view not in data_access.VIEW_SHEETS:
        raise OrchestratorError(
            f"The assistant selected an unknown view ('{view}'). Please rephrase your request."
        )

    chart_type = (payload.get("chart_type") or "bar").lower()
    if chart_type not in VALID_CHART_TYPES:
        chart_type = "bar"

    filters = query.get("filters") or []
    if not isinstance(filters, list):
        filters = []
    group_by = query.get("group_by") or []
    if isinstance(group_by, str):
        group_by = [group_by]

    return {
        "understood": payload.get("understood", ""),
        "limitations": [str(x) for x in payload.get("limitations", []) if x],
        "dependencies": [str(x) for x in payload.get("dependencies", []) if x],
        "chart_type": chart_type,
        "query": {
            "view": view,
            "filters": filters,
            "group_by": [g for g in group_by if g],
            "measure": query.get("measure"),
            "aggregation": (query.get("aggregation") or "sum").lower(),
            "sort_desc": bool(query.get("sort_desc", True)),
            "limit": query.get("limit"),
        },
    }


def interpret(session: Session, user_query: str, provider: llm_client.LLMProvider) -> dict[str, Any]:
    """Step 1: understand the request and state caveats. Builds nothing yet."""
    if not user_query.strip():
        raise OrchestratorError("Please describe the view you would like to see.")

    session.user_query = user_query.strip()
    session.log("user", session.user_query)

    interpretation = _interpret_with_retry(
        provider,
        prompts.interpretation_system_prompt(),
        [{"role": "user", "content": session.user_query}],
    )

    session.interpretation = interpretation
    session.state = AWAITING_CONFIRMATION
    session.log("assistant", interpretation["understood"])
    return {
        "state": session.state,
        "understood": interpretation["understood"],
        "limitations": interpretation["limitations"],
        "dependencies": interpretation["dependencies"],
        "query_summary": _summarise_query(interpretation["query"], interpretation["chart_type"]),
    }


def _dry_run(query: dict[str, Any]) -> None:
    """Validate the query against the real data before asking for confirmation."""
    try:
        data_access.run_query(**query)
    except data_access.QueryError as exc:
        raise OrchestratorError(f"That view cannot be built from this data: {exc}") from exc


def _interpret_with_retry(
    provider: llm_client.LLMProvider,
    system: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Interpret, then dry-run. If the query is invalid, hand the model the actual
    error once so it can correct itself - typically a wrong column name.
    """
    payload = provider.complete_json(system, messages)
    interpretation = _validate_interpretation(payload)
    try:
        _dry_run(interpretation["query"])
        return interpretation
    except OrchestratorError as first_error:
        retry_messages = [
            *messages,
            {"role": "assistant", "content": str(payload)},
            {"role": "user", "content":
                f"That query failed with: {first_error}\n\n"
                "Correct it and return the same JSON structure. Use only column "
                "names listed for the view you chose. Remember that control names "
                "(such as value date on the Client View) are not columns and cannot "
                "be filtered on."},
        ]
        retried = _validate_interpretation(provider.complete_json(system, retry_messages))
        _dry_run(retried["query"])  # a second failure is reported to the user
        return retried


def _summarise_query(query: dict[str, Any], chart_type: str) -> str:
    """Plain-English echo of the query, so the user can sanity-check it."""
    measure = query.get("measure") or "number of rows"
    agg = query.get("aggregation", "sum")
    parts = [f"{agg} of {measure}", f"from the {data_access.VIEW_SHEETS[query['view']]}"]
    if query.get("group_by"):
        parts.append("grouped by " + ", ".join(query["group_by"]))
    if query.get("filters"):
        parts.append("filtered where " + "; ".join(
            f"{f.get('column')} {f.get('operator', 'eq')} {f.get('value')}"
            for f in query["filters"]))
    parts.append(f"shown as a {chart_type}" if chart_type != "table" else "shown as a table")
    return ", ".join(parts) + "."


def build_report(session: Session, provider: llm_client.LLMProvider) -> dict[str, Any]:
    """Step 2: run the query for real, render, and have the model comment on the
    actual figures."""
    if not session.interpretation:
        raise OrchestratorError("There is nothing to build yet - ask for a view first.")

    interp = session.interpretation
    try:
        result = data_access.run_query(**interp["query"])
    except data_access.QueryError as exc:
        raise OrchestratorError(str(exc)) from exc

    table = result["table"]
    title = _title_for(interp)
    chart = report_builder.build_chart(table, interp["chart_type"], title, EXPORT_DIR, session.id)
    display_table = report_builder.format_table_for_display(table)

    narrative = _narrative(provider, session, interp, display_table, result["provenance"])

    session.report = {
        "title": title,
        "user_query": session.user_query,
        "understood": interp["understood"],
        "narrative": narrative,
        "limitations": interp["limitations"],
        "dependencies": interp["dependencies"],
        "chart_path": chart["chart_path"],
        "chart_type": chart["chart_type"],
        "chart_notes": chart["notes"],
        "table": display_table,
        "provenance": result["provenance"],
        "history": session.history,
    }
    session.state = REPORT_BUILT
    session.log("assistant", narrative)
    return _report_payload(session)


def _title_for(interp: dict[str, Any]) -> str:
    query = interp["query"]
    measure = query.get("measure") or "Row count"
    agg = query.get("aggregation", "sum").title()
    title = f"{agg} of {measure}" if query.get("measure") else "Count of records"
    if query.get("group_by"):
        title += " by " + ", ".join(query["group_by"])
    return title


def _narrative(
    provider: llm_client.LLMProvider,
    session: Session,
    interp: dict[str, Any],
    display_table: dict[str, Any],
    provenance: dict[str, Any],
) -> str:
    """Ask the model to comment on the computed figures only."""
    if not display_table.get("rows"):
        return ("No rows matched this query, so there is nothing to report. "
                "Consider widening the filters or checking the value date.")

    rows_text = "\n".join(
        " | ".join(str(v) for v in row) for row in display_table["rows"][:40]
    )
    content = f"""Original request: {session.user_query}

What the report shows: {interp['understood']}

Computed figures (columns: {', '.join(display_table['columns'])}):
{rows_text}

Rows in source view: {provenance['rows_in_view']}; after filters: {provenance['rows_after_filters']}.
"""
    try:
        return provider.complete_text(prompts.narrative_system_prompt(),
                                      [{"role": "user", "content": content}])
    except llm_client.LLMError:
        # A missing narrative must not lose the user their report.
        return ("Commentary could not be generated for this report. The figures below "
                "are complete and were computed directly from the source data.")


def refine(session: Session, instruction: str, provider: llm_client.LLMProvider) -> dict[str, Any]:
    """Step 3: apply a fine-tuning instruction and rebuild."""
    if session.state != REPORT_BUILT:
        raise OrchestratorError("Build a report before refining it.")
    if not instruction.strip():
        raise OrchestratorError("Please describe the change you would like.")

    session.log("user", instruction.strip())
    content = f"""Current query specification:
{session.interpretation['query']}

Current chart type: {session.interpretation['chart_type']}

The user's refinement instruction: {instruction.strip()}"""

    interpretation = _interpret_with_retry(
        provider, prompts.refine_system_prompt(), [{"role": "user", "content": content}]
    )
    session.interpretation = interpretation
    return build_report(session, provider)


def export(session: Session) -> dict[str, Any]:
    """Step 4: write the PDF and its Markdown companion."""
    if session.state != REPORT_BUILT or not session.report:
        raise OrchestratorError("There is no report to export yet.")

    report = dict(session.report)
    report["history"] = session.history
    if report.get("chart_notes"):
        report["chart_notes_md"] = "\n".join(f"> {n}" for n in report["chart_notes"])

    stamp = dt.datetime.now()
    pdf_path = pdf_export.build_pdf(report, EXPORT_DIR, session.id, stamp)
    md_path = md_export.build_markdown(report, EXPORT_DIR, session.id, stamp)
    return {"pdf": pdf_path.name, "markdown": md_path.name,
            "message": "Report exported. The Markdown file documents everything "
                       "considered, so a reader can give it to an AI to ask follow-up questions."}


def _report_payload(session: Session) -> dict[str, Any]:
    report = session.report
    chart_path = report.get("chart_path")
    return {
        "state": session.state,
        "title": report["title"],
        "narrative": report["narrative"],
        "understood": report["understood"],
        "limitations": report["limitations"],
        "dependencies": report["dependencies"],
        "chart_url": f"/api/chart/{Path(chart_path).name}" if chart_path else None,
        "chart_notes": report["chart_notes"],
        "table": report["table"],
        "provenance": report["provenance"],
    }
