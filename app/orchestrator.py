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

from . import (
    data_access,
    data_export,
    headline,
    llm_client,
    md_export,
    pdf_export,
    prompts,
    report_builder,
    saved_views,
)

# Overridable so a hosted deployment can write to a writable volume (e.g. /tmp)
# when the application directory is read-only.
EXPORT_DIR = Path(
    os.getenv("EXPORT_DIR") or Path(__file__).resolve().parent.parent / "exports"
)

AWAITING_QUERY = "awaiting_query"
AWAITING_CONFIRMATION = "awaiting_confirmation"
REPORT_BUILT = "report_built"

VALID_CHART_TYPES = {"bar", "barh", "line", "table"}

# The internal names are matplotlib's. Never show them to a reader.
CHART_TYPE_LABELS = {
    "bar": "a column chart",
    "barh": "a horizontal bar chart",
    "line": "a line chart over time",
    "table": "a table",
    "stat": "a single headline figure",
    "distribution": "a distribution chart",
}


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
        self.last_table: Any = None  # raw result, for re-rendering the print chart
        self.last_result: dict[str, Any] | None = None  # full query result

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

    def as_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        return [v for v in (value or []) if v]

    mode = (query.get("mode") or "aggregate").lower()
    if mode not in {"aggregate", "list", "distribution", "peak", "quality",
                    "backlog"}:
        mode = "aggregate"

    measures = query.get("measures") or []
    if isinstance(measures, dict):
        measures = [measures]
    # Tolerate the older single-measure form.
    if not measures and (query.get("measure") or query.get("aggregation")):
        measures = [{"column": query.get("measure"),
                     "aggregation": query.get("aggregation") or "sum"}]

    # A listing that renders as a chart makes no sense; force the table.
    if mode == "list":
        chart_type = "table"

    limit = query.get("limit")
    if isinstance(limit, str) and limit.isdigit():
        limit = int(limit)
    if not isinstance(limit, int):
        limit = None

    unavailable = []
    for item in payload.get("unavailable") or []:
        if isinstance(item, str):
            unavailable.append({"concept": item, "reason": "", "needed": ""})
        elif isinstance(item, dict) and item.get("concept"):
            unavailable.append({
                "concept": str(item.get("concept", "")),
                "reason": str(item.get("reason", "")),
                "needed": str(item.get("needed", "")),
            })

    return {
        "understood": payload.get("understood", ""),
        "limitations": [str(x) for x in payload.get("limitations", []) if x],
        "dependencies": [str(x) for x in payload.get("dependencies", []) if x],
        "unavailable": unavailable,
        "chart_type": chart_type,
        "query": {
            "view": view,
            "mode": mode,
            "filters": filters,
            "group_by": as_list(query.get("group_by")),
            "time_bucket": query.get("time_bucket") or None,
            "measures": measures,
            "columns": as_list(query.get("columns")),
            "sort_by": query.get("sort_by"),
            "sort_desc": bool(query.get("sort_desc", True)),
            "limit": limit,
            "join": query.get("join") or None,
            "derived": query.get("derived") or [],
            "rate": query.get("rate") or None,
            "backlog": query.get("backlog") or None,
            "add_share_of_total": bool(query.get("add_share_of_total", False)),
            "add_cumulative": bool(query.get("add_cumulative", False)),
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
        "unavailable": interpretation.get("unavailable", []),
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


AGG_WORDS = {
    "sum": "total", "mean": "average", "median": "median", "count": "number of",
    "min": "lowest", "max": "highest", "std": "spread (standard deviation) of",
    "var": "variance of",
}
OPERATOR_WORDS = {
    "eq": "is", "ne": "is not", "gt": "is above", "gte": "is at least",
    "lt": "is below", "lte": "is at most", "in": "is one of",
    "not_in": "is not one of", "contains": "contains",
    "is_null": "is blank", "not_null": "is not blank",
}


def _describe_filter(spec: dict[str, Any]) -> str:
    """One filter in English, including the any/all groups.

    Without handling the group form this rendered as "None is None", which tells
    a reader nothing about what was actually counted.
    """
    for key, joiner in (("any", " or "), ("all", " and ")):
        if key in spec:
            parts = [_describe_filter(p) for p in spec.get(key) or []]
            parts = [p for p in parts if p]
            if not parts:
                return ""
            body = joiner.join(parts)
            return f"({body})" if len(parts) > 1 else body

    column = spec.get("column")
    if not column:
        return ""
    op = OPERATOR_WORDS.get((spec.get("operator") or "eq").lower(), spec.get("operator"))
    if op in ("is blank", "is not blank"):
        return f"{column} {op}"
    value = spec.get("value")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return f"{column} {op} {value}"


def _summarise_query(query: dict[str, Any], chart_type: str) -> str:
    """Plain-English echo of the query, so the user can sanity-check it.

    Deliberately free of internal vocabulary: no matplotlib chart names, no
    operator codes. The reader is checking whether the assistant understood
    them, which they cannot do if the answer is written in jargon.
    """
    view_name = data_access.VIEW_SHEETS[query["view"]].replace(" View", "")
    parts: list[str] = []

    if query.get("mode") == "backlog":
        spec = query.get("backlog") or {}
        parts.append(f"How many {view_name} items were outstanding at each "
                     f"{spec.get('granularity', '15min')} through the period, "
                     "with arrivals and clearances")
    elif query.get("mode") == "quality":
        parts.append(f"How completely each column of {view_name} is populated, "
                     "least complete first")
    elif query.get("mode") == "peak":
        measures = query.get("measures") or []
        target = (measures[0].get("column") if measures else None) or "the position"
        parts.append(f"The peak and lowest intraday position of {target} "
                     f"in {view_name}, built up through each day")
        if query.get("group_by"):
            parts.append("separately for each " + " and ".join(query["group_by"]))
    elif query.get("mode") == "distribution":
        measures = query.get("measures") or []
        target = (measures[0].get("column") if measures else None) or "the values"
        parts.append(f"How {target} is distributed across {view_name} records")
        if query.get("group_by"):
            parts.append("compared by " + " and ".join(query["group_by"]))
    elif query.get("mode") == "list":
        cols = query.get("columns") or []
        parts.append(f"A list of {view_name} records")
        if cols:
            shown = ", ".join(cols[:5])
            parts.append(f"showing {shown}"
                         + (f" and {len(cols) - 5} more columns" if len(cols) > 5 else ""))
    else:
        measures = query.get("measures") or []
        described = []
        for m in measures:
            agg = AGG_WORDS.get((m.get("aggregation") or "sum").lower(),
                                m.get("aggregation") or "sum")
            described.append(f"{agg} {m.get('column')}" if m.get("column")
                             else "number of records")
        parts.append("The " + " and ".join(described or ["total"])
                     + f" in {view_name}")
        if query.get("time_bucket"):
            tb = query["time_bucket"]
            parts.append(f"broken down by {tb.get('granularity', 'hour')}")
        if query.get("group_by"):
            parts.append("split by " + " and ".join(query["group_by"]))

    if query.get("filters"):
        readable = [_describe_filter(f) for f in query["filters"]]
        parts.append("counting only where " + " and ".join(r for r in readable if r))
    if query.get("add_share_of_total"):
        parts.append("with each row's share of the total")
    if query.get("add_cumulative"):
        parts.append("with a running total")
    if query.get("join"):
        hops = query["join"] if isinstance(query["join"], list) else [query["join"]]
        names = [h.get("view", "") for h in hops if isinstance(h, dict)]
        if names:
            parts.append("combined with " + " then ".join(names))
    if query.get("limit"):
        parts.append(f"limited to the top {query['limit']}")

    parts.append("presented as " + CHART_TYPE_LABELS.get(chart_type, "a chart"))
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
    # The browser gets the dark theme; the PDF re-renders light at export time.
    chart = _render_for(result, interp, title, session, theme="dark")
    display_table = report_builder.format_table_for_display(table)
    # Kept so export can re-render the chart for print without re-querying.
    session.last_table = table

    narrative = _narrative(provider, session, interp, display_table, result["provenance"])

    # A correction the engine made to keep the arithmetic valid is a limitation
    # the reader must see, not a footnote in the provenance block.
    limitations = list(interp["limitations"])
    for note in result["provenance"].get("currency_corrections") or []:
        if note not in limitations:
            limitations.insert(0, note)

    session.last_result = result
    session.report = {
        "headline": headline.build(result, result["provenance"]),
        "title": title,
        "user_query": session.user_query,
        "understood": interp["understood"],
        "narrative": narrative,
        "limitations": limitations,
        "unavailable": interp.get("unavailable", []),
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


def _render_for(result: dict[str, Any], interp: dict[str, Any], title: str,
                session: Session, theme: str, also_svg: bool = False) -> dict[str, Any]:
    """Pick the right renderer for the query mode."""
    if result.get("mode") == "distribution" and result.get("raw_values") is not None:
        raw = result["raw_values"]
        column = result["provenance"].get("measure")
        labels = result.get("label_columns") or []
        group_col = labels[0] if labels and labels[0] in raw.columns else None
        return report_builder.render_distribution(
            raw, column, title, EXPORT_DIR, session.id,
            theme=theme, group_col=group_col, also_svg=also_svg,
        )
    return report_builder.build_chart(
        result["table"], interp["chart_type"], title, EXPORT_DIR, session.id,
        theme=theme, measure_columns=result.get("measure_columns"),
        label_columns=result.get("label_columns"), also_svg=also_svg,
    )


def _title_for(interp: dict[str, Any]) -> str:
    query = interp["query"]
    view_name = data_access.VIEW_SHEETS[query["view"]].replace(" View", "")

    if query.get("mode") == "backlog":
        spec = query.get("backlog") or {}
        return (f"Queue outstanding over time, every "
                f"{spec.get('granularity', '15min')}")

    if query.get("mode") == "quality":
        return f"Data completeness - {view_name}"

    if query.get("mode") == "peak":
        title = "Peak intraday position"
        if query.get("group_by"):
            title += " by " + ", ".join(query["group_by"])
        return title

    if query.get("mode") == "distribution":
        measures = query.get("measures") or []
        target = (measures[0].get("column") if measures else None) or "values"
        title = f"Distribution of {target}"
        if query.get("group_by"):
            title += " by " + ", ".join(query["group_by"])
        return title

    if query.get("mode") == "list":
        title = f"{view_name} records"
        if query.get("limit"):
            title = f"Top {query['limit']} {view_name.lower()} records"
        if query.get("sort_by"):
            title += f" by {query['sort_by']}"
        return title

    measures = query.get("measures") or []
    named = [m.get("column") for m in measures if m.get("column")]
    if not named:
        title = "Count of records"
    elif len(named) == 1:
        agg = (measures[0].get("aggregation") or "sum").title()
        title = f"{agg} of {named[0]}"
    else:
        title = " vs ".join(named)

    grouping = list(query.get("group_by") or [])
    if query.get("time_bucket"):
        tb = query["time_bucket"]
        grouping = [f"{tb.get('column')} ({tb.get('granularity', 'hour')})"] + grouping
    if grouping:
        title += " by " + ", ".join(grouping)
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


def save_current(session: Session, name: str, description: str = "",
                 overwrite: bool = False) -> dict[str, Any]:
    """Store the current view's specification under a name."""
    if not session.interpretation:
        raise OrchestratorError("Build a view before saving it.")
    interp = session.interpretation
    record = saved_views.save_view(
        name=name,
        query=interp["query"],
        chart_type=interp["chart_type"],
        user_query=session.user_query,
        understood=interp.get("understood", ""),
        limitations=interp.get("limitations", []),
        dependencies=interp.get("dependencies", []),
        description=description,
        overwrite=overwrite,
    )
    return {"id": record["id"], "name": record["name"],
            "message": f"Saved as '{record['name']}'."}


def load_saved(session: Session, view_id: str,
               provider: llm_client.LLMProvider) -> dict[str, Any]:
    """Re-run a saved view against the current data.

    The stored specification is replayed rather than the stored results, so a
    saved view always reflects the data as it stands now.
    """
    record = saved_views.get_view(view_id)
    session.user_query = record.get("user_query") or record["name"]
    session.interpretation = {
        "understood": record.get("understood", ""),
        "limitations": record.get("limitations", []),
        "dependencies": record.get("dependencies", []),
        "chart_type": record.get("chart_type", "bar"),
        "query": record["query"],
    }
    session.log("user", f"Loaded saved view '{record['name']}'")
    # Fail loudly if the data has moved on and the saved query no longer runs.
    try:
        _dry_run(session.interpretation["query"])
    except OrchestratorError as exc:
        raise OrchestratorError(
            f"The saved view '{record['name']}' no longer runs against this data: {exc}"
        ) from exc

    session.state = REPORT_BUILT
    payload = build_report(session, provider)
    payload["loaded_view"] = {"id": record["id"], "name": record["name"]}
    return payload


def export(session: Session) -> dict[str, Any]:
    """Step 4: write the PDF and its Markdown companion."""
    if session.state != REPORT_BUILT or not session.report:
        raise OrchestratorError("There is no report to export yet.")

    report = dict(session.report)
    report["history"] = session.history
    if report.get("chart_notes"):
        report["chart_notes_md"] = "\n".join(f"> {n}" for n in report["chart_notes"])

    # Re-render the chart light for print. The on-screen chart is dark, which
    # would waste ink and read wrong on paper.
    svg_name = None
    if session.last_result is not None and report.get("chart_path"):
        print_chart = _render_for(session.last_result, session.interpretation,
                                  report["title"], session, theme="light",
                                  also_svg=True)
        if print_chart.get("chart_path"):
            report["chart_path"] = print_chart["chart_path"]
        if print_chart.get("svg_path"):
            svg_name = Path(print_chart["svg_path"]).name

    stamp = dt.datetime.now()
    pdf_path = pdf_export.build_pdf(report, EXPORT_DIR, session.id, stamp)
    md_path = md_export.build_markdown(report, EXPORT_DIR, session.id, stamp)

    xlsx_name = None
    if session.last_table is not None:
        result = session.last_result or {}
        xlsx_path = data_export.build_workbook(
            report, session.last_table, EXPORT_DIR, session.id, stamp,
            measure_columns=result.get("measure_columns"),
            label_columns=result.get("label_columns"),
        )
        xlsx_name = xlsx_path.name

    return {
        "pdf": pdf_path.name,
        "markdown": md_path.name,
        "excel": xlsx_name,
        "svg": svg_name,
        "message": "Report exported. The Markdown documents everything considered, "
                   "so a reader can give it to an AI to ask follow-up questions. "
                   "The Excel carries the prepared data with an editable chart.",
    }


def _report_payload(session: Session) -> dict[str, Any]:
    report = session.report
    chart_path = report.get("chart_path")
    return {
        "state": session.state,
        "title": report["title"],
        "headline": report.get("headline", []),
        "narrative": report["narrative"],
        "understood": report["understood"],
        "limitations": report["limitations"],
        "dependencies": report["dependencies"],
        "unavailable": report.get("unavailable", []),
        "chart_url": f"/api/chart/{Path(chart_path).name}" if chart_path else None,
        "chart_notes": report["chart_notes"],
        "table": report["table"],
        "provenance": report["provenance"],
    }
