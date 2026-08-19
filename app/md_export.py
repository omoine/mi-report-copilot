"""Markdown companion export.

This file is the point of the whole feature: a reader who questions a figure in
the PDF should be able to hand this Markdown to any AI and get an accurate
answer. So it must be self-contained - the question asked, how it was
interpreted, the exact query executed, the figures, the caveats, the relevant
data-dictionary entries, the controls in effect, and the full refinement history.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from . import data_access


def _fmt_filters(filters: list[dict[str, Any]]) -> str:
    if not filters:
        return "_None - the full view was used._"
    return "\n".join(
        f"- `{f.get('column')}` {f.get('operator', 'eq')} `{f.get('value')}`" for f in filters
    )


def _headline_block(items: list[dict[str, str]] | None) -> str:
    """The two or three numbers the report leads with."""
    if not items:
        return ""
    lines = ["| | |", "|---|---|"]
    lines += [f"| **{i['label']}** | **{i['value']}** — {i.get('detail', '')} |"
              for i in items]
    return "\n".join(lines)


def _fmt_unavailable(items: list[dict[str, str]] | None) -> str:
    """Concepts the data cannot supply, stated rather than left to be noticed."""
    if not items:
        return "_Nothing was asked for that this data cannot supply._"
    lines = []
    for item in items:
        lines.append(f"- **{item.get('concept', '')}**"
                     + (f" — {item['reason']}" if item.get("reason") else ""))
        if item.get("needed"):
            lines.append(f"  - *Would need:* {item['needed']}")
    return "\n".join(lines)


def _fmt_list(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"_{empty}_"


def _table_markdown(table: dict[str, Any]) -> str:
    if not table or not table.get("columns"):
        return "_No rows returned._"
    cols = table["columns"]
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in table["rows"])
    if table.get("truncated"):
        lines.append(f"\n_Showing {len(table['rows'])} of {table['total_rows']} rows._")
    return "\n".join(lines)


def _relevant_dictionary(view: str, columns: list[str]) -> str:
    """Only the data-dictionary entries actually involved in this report."""
    view_label = data_access.DICTIONARY_LABELS.get(view, view).casefold()
    wanted = {str(c).strip().casefold() for c in columns}
    rows = [
        r for r in data_access.get_metadata()["data_dictionary"]
        if str(r.get("View", "")).strip().casefold() == view_label
        and str(r.get("Column Name", "")).strip().casefold() in wanted
    ]
    if not rows:
        return "_No matching data-dictionary entries._"

    out = []
    for r in rows:
        parts = [f"**{r.get('Column Name')}** ({r.get('Data Type')})"]
        if r.get("Definition / Business Rule"):
            parts.append(f"  - Definition: {r['Definition / Business Rule']}")
        if str(r.get("Derived", "")).strip().lower() == "yes":
            parts.append(f"  - Derived value. Formula: `{r.get('Formula / Calculation')}`")
        if r.get("Allowed Values / Domain"):
            parts.append(f"  - Domain: {r['Allowed Values / Domain']}")
        if r.get("Nullable"):
            parts.append(f"  - Nullable: {r['Nullable']}")
        out.append("\n".join(parts))
    return "\n\n".join(out)


def _controls_for_view(view: str) -> str:
    view_label = data_access.DICTIONARY_LABELS.get(view, view).casefold()
    rows = [
        r for r in data_access.get_metadata()["view_controls"]
        if str(r.get("View", "")).strip().casefold() == view_label
    ]
    if not rows:
        return "_No recorded controls for this view._"
    return "\n".join(
        f"- **{r.get('Control / Filter')}** = `{r.get('Synthetic Value')}` - {r.get('Definition')}"
        for r in rows
    )


def _fmt_history(history: list[dict[str, str]]) -> str:
    if not history:
        return "_No refinement steps._"
    return "\n".join(
        f"{i}. **{h.get('role', 'user').title()}:** {h.get('content', '')}"
        for i, h in enumerate(history, start=1)
    )


def build_markdown(report: dict[str, Any], out_dir: Path, session_id: str,
                   stamp: dt.datetime | None = None) -> Path:
    """Write the companion document to disk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Shared with the PDF so the two files are named as a matching pair.
    stamp = stamp or dt.datetime.now()
    path = out_dir / f"mi_report_{session_id}_{stamp.strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(render_markdown(report, stamp), encoding="utf-8")
    return path


def render_markdown(report: dict[str, Any], stamp: dt.datetime | None = None) -> str:
    """The companion document as text.

    Separated from writing it so the in-app assistant can be grounded in exactly
    this string. If the assistant were given its own summary of the report, the
    two could describe the same figures differently - here they cannot, because
    they are the same document.
    """
    stamp = stamp or dt.datetime.now()

    prov = report.get("provenance", {})
    view = prov.get("view", "")
    involved = list(prov.get("group_by") or [])
    if prov.get("measure"):
        involved.append(prov["measure"])
    involved.extend(f.get("column") for f in prov.get("filters") or [])
    involved = [c for c in involved if c]

    meta = data_access.get_metadata()
    fx = ", ".join(f"{k}={v}" for k, v in meta["fx_rates"].items())

    content = f"""# {report.get('title', 'MI Report')} - Supporting Documentation

> This document accompanies the PDF report of the same name. It records everything
> that was considered in producing that report. If you have a question about the
> report, you can give this file to an AI assistant and it will have the full
> context needed to answer accurately.

**Generated:** {stamp.strftime('%d %B %Y at %H:%M:%S')}
**Data classification:** {meta['data_classification']}
**Source file:** `{prov.get('source_file', data_access.DATA_FILE.name)}`

---

## 1. What was asked

> {report.get('user_query', '_Not recorded._')}

## 2. How the request was interpreted

{report.get('understood', '_Not recorded._')}

## 3. The finding

{_headline_block(report.get('headline'))}

{report.get('narrative', '_No commentary recorded._')}

## 4. Limitations of this view

These are the caveats a reader should understand before acting on the figures above.

{_fmt_list(report.get('limitations', []), 'No limitations recorded.')}

## 4b. What this view cannot tell you

Parts of the question the data cannot answer at all. These are gaps in what is
captured, not caveats about the figures above - no amount of re-querying will
produce them.

{_fmt_unavailable(report.get('unavailable'))}

## 5. Dependencies

What this view depends on, and what would change the answer.

{_fmt_list(report.get('dependencies', []), 'No dependencies recorded.')}

## 6. The exact query that produced these figures

No figure in this report was estimated or generated by a language model. Every
number was computed by a deterministic query over the source data, specified as:

| Parameter | Value |
|---|---|
| View | `{view}` (sheet: {prov.get('sheet', '')}) |
| Aggregation | `{prov.get('aggregation', '')}` |
| Measure | `{prov.get('measure') or 'row count'}` |
| Grouped by | {', '.join(f'`{g}`' for g in prov.get('group_by') or []) or '_not grouped_'} |
| Rows in view | {prov.get('rows_in_view', '?')} |
| Rows after filters | {prov.get('rows_after_filters', '?')} |
| Rows returned | {prov.get('rows_returned', '?')} |
| Executed at | {prov.get('executed_at', '')} |

**Filters applied:**

{_fmt_filters(prov.get('filters') or [])}

## 7. The result data

{_table_markdown(report.get('table', {}))}

{report.get('chart_notes_md', '')}

## 8. Definitions of the fields used

{_relevant_dictionary(view, involved)}

## 9. Controls in effect on the source view

The source screen applies these controls. They scope what the underlying data
contains, and therefore what this report can show.

{_controls_for_view(view)}

## 10. FX rates used for display-currency translation

Any column labelled "(Display)" was translated into the display currency using
these static rates. They are synthetic and illustrative - they are not live
market rates, and they do not vary by date.

{fx}

## 11. How this was produced

1. The user described the view they wanted in natural language.
2. An AI assistant interpreted that request into a structured query specification
   and stated its limitations and dependencies. The user confirmed before the
   report was built.
3. The query was executed deterministically over the source data. **The AI did not
   produce, estimate or adjust any figure.**
4. The AI wrote the commentary in section 3 based only on the computed figures.
5. The user optionally refined the report; the full sequence is below.

**Interaction history:**

{_fmt_history(report.get('history', []))}

---

## Notes for an AI answering questions about this report

- Answer only from this document. If the answer is not here, say so rather than
  inferring it.
- The figures in section 7 are authoritative; section 3 is commentary about them.
- Always carry the limitations in section 4 into any answer that uses these figures.
- This is synthetic, non-production data. Do not present it as a real position.
"""

    return content
