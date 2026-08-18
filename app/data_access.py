"""Deterministic data access over the synthetic liquidity workbook.

Every figure that reaches a report comes from this module. The LLM chooses
*which* query to run (view, filters, grouping, measure); it never supplies or
computes the numbers themselves. That keeps reconciliation and ledger figures
auditable and reproducible.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "synthetic_liquidity_views.xlsx"

# All three view sheets share the same layout: title row, two control rows,
# two blank rows, then the header on row 6. The final row is a "Total" footer
# that must not be treated as data.
HEADER_ROW = 6
TOTAL_ROW_MARKER = "Total"

VIEW_SHEETS = {
    "nostro_transfer": "Nostro Transfer View",
    "client": "Client View",
    "business_ledger": "Business Ledger Txn View",
}

# The Data Dictionary and View Controls sheets label the third view
# "Business Ledger Transaction View" while its sheet tab is abbreviated to
# "Txn". Metadata lookups must use these labels, not the sheet names.
DICTIONARY_LABELS = {
    "nostro_transfer": "Nostro Transfer View",
    "client": "Client View",
    "business_ledger": "Business Ledger Transaction View",
}

# Those two sheets carry a title, a subtitle and a blank row before the header.
METADATA_HEADER_ROW = 4

# Some columns hold glyphs (the reconciliation Match tick/cross). A language
# model cannot reliably reproduce these characters, so plain-word aliases are
# accepted for them and normalised back to the stored glyph before filtering.
GLYPH_ALIASES = {
    "✓": ["matched", "match", "true", "yes", "y", "pass", "passed", "ok", "tick", "check"],
    "✕": ["unmatched", "not matched", "no match", "false", "no", "n", "fail",
               "failed", "mismatch", "mismatched", "break", "cross", "x"],
}
_ALIAS_LOOKUP = {alias: glyph for glyph, aliases in GLYPH_ALIASES.items() for alias in aliases}

# Column carrying the primary monetary measure for each view, used when the
# caller asks to aggregate but does not name a measure.
DEFAULT_MEASURE = {
    "nostro_transfer": "Value Amount",
    "client": "Calculated Balance (Display)",
    "business_ledger": "Amount (Display)",
}


class QueryError(ValueError):
    """Raised when a requested query cannot be satisfied by the data."""


def _clean_frame(rows: list[list[Any]], header: list[Any]) -> pd.DataFrame:
    cols = [str(c).strip() for c in header]
    df = pd.DataFrame(rows, columns=cols)
    # Drop the trailing "Total" footer row and any fully-empty rows.
    if len(df) and str(df.iloc[-1, 0]).strip() == TOTAL_ROW_MARKER:
        df = df.iloc[:-1]
    df = df.dropna(how="all")
    return df.reset_index(drop=True)


@lru_cache(maxsize=1)
def _workbook_frames() -> dict[str, pd.DataFrame]:
    """Load the three view sheets. data_only=True reads Excel's cached formula
    results, so derived columns (balances, FX translation, Match) are available
    without needing Excel installed."""
    wb = openpyxl.load_workbook(DATA_FILE, data_only=True)
    frames: dict[str, pd.DataFrame] = {}
    try:
        for view, sheet in VIEW_SHEETS.items():
            ws = wb[sheet]
            all_rows = list(ws.iter_rows(min_row=HEADER_ROW, values_only=True))
            frames[view] = _clean_frame([list(r) for r in all_rows[1:]], list(all_rows[0]))
    finally:
        wb.close()
    return frames


@lru_cache(maxsize=1)
def _metadata() -> dict[str, Any]:
    """Read the workbook's own Data Dictionary, View Controls and Reference Data.

    This is what grounds the assistant's limitations narrative: business rules,
    domains and derivation formulas come from the client's own documentation
    rather than being invented by the model.
    """
    wb = openpyxl.load_workbook(DATA_FILE, data_only=True)
    try:
        dd_rows = list(wb["Data Dictionary"].iter_rows(min_row=METADATA_HEADER_ROW, values_only=True))
        dd_header = [str(c).strip() for c in dd_rows[0]]
        data_dictionary = [
            dict(zip(dd_header, r)) for r in dd_rows[1:] if r and r[0] is not None
        ]

        vc_rows = list(wb["View Controls"].iter_rows(min_row=METADATA_HEADER_ROW, values_only=True))
        vc_header = [str(c).strip() for c in vc_rows[0]]
        view_controls = [
            dict(zip(vc_header, r)) for r in vc_rows[1:] if r and r[0] is not None
        ]

        fx: dict[str, float] = {}
        for row in wb["Reference Data"].iter_rows(min_row=3, values_only=True):
            if row and row[0] and isinstance(row[1], (int, float)):
                fx[str(row[0]).strip()] = float(row[1])
    finally:
        wb.close()

    return {
        "data_dictionary": data_dictionary,
        "view_controls": view_controls,
        "fx_rates": fx,
        "data_classification": "Synthetic / non-production",
    }


def get_metadata() -> dict[str, Any]:
    return _metadata()


def get_frame(view: str) -> pd.DataFrame:
    if view not in VIEW_SHEETS:
        raise QueryError(
            f"Unknown view '{view}'. Available views: {', '.join(VIEW_SHEETS)}."
        )
    return _workbook_frames()[view].copy()


def list_columns(view: str) -> list[str]:
    return list(get_frame(view).columns)


def schema_summary() -> dict[str, Any]:
    """Compact schema for the model's system prompt: columns, dtypes, and the
    distinct values of low-cardinality (enum-like) columns."""
    summary: dict[str, Any] = {}
    for view in VIEW_SHEETS:
        df = get_frame(view)
        cols = []
        for col in df.columns:
            series = df[col].dropna()
            entry: dict[str, Any] = {"name": col, "dtype": _friendly_dtype(series)}
            # Surface enum domains so the model filters using real values.
            # Checked via the friendly dtype rather than `== object`, because
            # pandas 3 gives text columns a dedicated StringDtype.
            if entry["dtype"] == "text" and 0 < series.nunique() <= 12:
                members = {str(v) for v in series.unique()}
                entry["values"] = sorted(members)
                # Glyph columns get word aliases, since a model cannot reliably
                # emit characters like the reconciliation tick and cross.
                if members.issubset(set(GLYPH_ALIASES)):
                    entry["filter_using_these_words_instead"] = {
                        GLYPH_ALIASES[g][0]: g for g in sorted(members)
                    }
            cols.append(entry)
        summary[view] = {
            "sheet": VIEW_SHEETS[view],
            "row_count": len(df),
            "default_measure": DEFAULT_MEASURE[view],
            "columns": cols,
        }
    return summary


def _friendly_dtype(series: pd.Series) -> str:
    """Classify by pandas dtype, not by isinstance on a sample: numpy scalars do
    not subclass the matching Python types (np.int64 is not an int)."""
    if series.empty:
        return "unknown (all values empty)"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return "number"
    sample = series.iloc[0]
    if isinstance(sample, dt.datetime):
        return "datetime"
    if isinstance(sample, dt.date):
        return "date"
    if isinstance(sample, (int, float)) and not isinstance(sample, bool):
        return "number"
    return "text"


def _coerce_comparable(value: Any, series: pd.Series) -> Any:
    """Align a filter value with the column's runtime type so comparisons work."""
    if series.empty:
        return value
    sample = series.dropna()
    if sample.empty:
        return value
    sample = sample.iloc[0]
    if isinstance(sample, (dt.datetime, dt.date)) and isinstance(value, str):
        try:
            return pd.to_datetime(value).to_pydatetime()
        except (ValueError, TypeError) as exc:
            raise QueryError(f"Could not read '{value}' as a date.") from exc
    if isinstance(sample, (int, float)) and isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise QueryError(f"Could not read '{value}' as a number.") from exc
    return value


def _normalise_glyph_value(value: Any, series: pd.Series) -> Any:
    """Map a word like 'unmatched' onto the glyph actually stored in the column.

    Only applies to columns whose values are glyphs, so ordinary text filtering
    is untouched. Also catches the control characters a model sometimes emits
    when it cannot reproduce a glyph: if the column is a glyph column and the
    value matches none of its members, that filter would silently return zero
    rows, which reads as a real finding of 'nothing'.
    """
    members = {str(v) for v in series.dropna().unique()}
    if not members or not members.issubset(set(GLYPH_ALIASES)):
        return value

    text = str(value).strip()
    if text in members:
        return text
    mapped = _ALIAS_LOOKUP.get(text.casefold())
    if mapped in members:
        return mapped
    raise QueryError(
        f"Value {text!r} is not valid for this column. Use one of: "
        + ", ".join(sorted(f"'{m}'" for m in members))
        + " - or the words "
        + ", ".join(sorted({a for g in members for a in GLYPH_ALIASES[g][:2]}))
        + "."
    )


def _apply_filter(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    col, op = spec.get("column"), (spec.get("operator") or "eq").lower()
    value = spec.get("value")
    if col not in df.columns:
        raise QueryError(
            f"Column '{col}' does not exist. Available columns: {', '.join(df.columns)}."
        )

    series = df[col]
    if op in {"eq", "ne"}:
        value = _normalise_glyph_value(value, series)
    if op in {"in", "not_in"}:
        values = value if isinstance(value, list) else [value]
        values = [str(v) for v in values]
        mask = series.astype(str).isin(values)
        return df[~mask if op == "not_in" else mask]

    if op == "contains":
        return df[series.astype(str).str.contains(str(value), case=False, na=False)]

    if op == "is_null":
        return df[series.isna()]
    if op == "not_null":
        return df[series.notna()]

    target = _coerce_comparable(value, series)
    ops = {
        "eq": lambda s: s == target,
        "ne": lambda s: s != target,
        "gt": lambda s: s > target,
        "gte": lambda s: s >= target,
        "lt": lambda s: s < target,
        "lte": lambda s: s <= target,
    }
    if op not in ops:
        raise QueryError(f"Unsupported operator '{op}'.")
    # Text columns compared with eq/ne should ignore case and padding.
    if op in {"eq", "ne"} and isinstance(target, str):
        mask = series.astype(str).str.strip().str.casefold() == target.strip().casefold()
        return df[~mask if op == "ne" else mask]
    return df[ops[op](series)]


AGGREGATIONS = {"sum", "mean", "count", "min", "max", "median"}

# Pandas offset aliases for time bucketing, keyed by the words a user would use.
TIME_GRANULARITIES = {
    "minute": ("min", "%Y-%m-%d %H:%M"),
    "15min": ("15min", "%Y-%m-%d %H:%M"),
    "30min": ("30min", "%Y-%m-%d %H:%M"),
    "hour": ("h", "%Y-%m-%d %H:00"),
    "day": ("D", "%Y-%m-%d"),
    "week": ("W", "%Y-%m-%d"),
    "month": ("MS", "%Y-%m"),
}


def _require_column(df: pd.DataFrame, col: str, role: str, view: str) -> None:
    if col not in df.columns:
        raise QueryError(
            f"Cannot use '{col}' as {role} - no such column. "
            f"Available columns: {', '.join(get_frame(view).columns)}."
        )


def _as_numeric(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if pd.api.types.is_numeric_dtype(df[col]):
        return df
    coerced = pd.to_numeric(df[col], errors="coerce")
    if coerced.notna().sum() == 0:
        raise QueryError(f"Column '{col}' is not numeric, so it cannot be aggregated.")
    return df.assign(**{col: coerced})


def _apply_time_bucket(
    df: pd.DataFrame, spec: dict[str, Any], view: str
) -> tuple[pd.DataFrame, str]:
    """Floor a timestamp column into buckets and return the new label column.

    Without this, grouping by a raw timestamp yields one group per row, which
    looks like a time series but carries no information.
    """
    col = spec.get("column")
    _require_column(df, col, "a time bucket", view)
    gran = str(spec.get("granularity") or "hour").lower()
    if gran not in TIME_GRANULARITIES:
        raise QueryError(
            f"Unsupported time granularity '{gran}'. "
            f"Use one of: {', '.join(TIME_GRANULARITIES)}."
        )
    freq, fmt = TIME_GRANULARITIES[gran]

    stamps = pd.to_datetime(df[col], errors="coerce")
    if stamps.notna().sum() == 0:
        raise QueryError(f"Column '{col}' does not contain readable timestamps.")

    label = f"{col} ({gran})"
    bucketed = stamps.dt.floor(freq) if freq not in {"W", "MS"} else stamps.dt.to_period(
        "W" if freq == "W" else "M"
    ).dt.start_time
    return df.assign(**{label: bucketed.dt.strftime(fmt)}), label


def _normalise_measures(
    measures: list[dict[str, Any]] | None, view: str, df: pd.DataFrame
) -> list[dict[str, str]]:
    """Coerce the measure list into [{column, aggregation, label}]."""
    out: list[dict[str, str]] = []
    for m in measures or []:
        if isinstance(m, str):
            m = {"column": m}
        agg = str(m.get("aggregation") or "sum").lower()
        if agg not in AGGREGATIONS:
            raise QueryError(f"Unsupported aggregation '{agg}'.")
        col = m.get("column")
        if agg == "count" and not col:
            out.append({"column": "", "aggregation": "count", "label": "count"})
            continue
        _require_column(df, col, "a measure", view)
        out.append({"column": col, "aggregation": agg, "label": col})
    return out


def run_query(
    view: str,
    mode: str = "aggregate",
    filters: list[dict[str, Any]] | None = None,
    group_by: list[str] | None = None,
    time_bucket: dict[str, Any] | None = None,
    measures: list[dict[str, Any]] | None = None,
    columns: list[str] | None = None,
    sort_by: str | None = None,
    sort_desc: bool = True,
    limit: int | None = None,
    # Accepted for convenience; folded into `measures`.
    measure: str | None = None,
    aggregation: str | None = None,
) -> dict[str, Any]:
    """Run a deterministic query and return the result plus a provenance record.

    Two modes:
      aggregate - group and summarise (optionally bucketing a timestamp)
      list      - return matching rows as they are, sorted and limited
    """
    df = get_frame(view)
    total_rows = len(df)

    filters = filters or []
    for spec in filters:
        df = _apply_filter(df, spec)
    rows_after_filters = len(df)

    mode = (mode or "aggregate").lower()
    if mode not in {"aggregate", "list"}:
        raise QueryError(f"Unsupported mode '{mode}'. Use 'aggregate' or 'list'.")

    # Fold the single-measure convenience form into the list form.
    if measure or aggregation:
        measures = measures or []
        if not measures:
            measures = [{"column": measure, "aggregation": aggregation or "sum"}]

    label_columns: list[str] = []
    measure_columns: list[str] = []
    measures_note: str | None = None

    if mode == "list":
        wanted = [c for c in (columns or []) if c]
        for col in wanted:
            _require_column(df, col, "a column", view)
        result = df[wanted].copy() if wanted else df.copy()
        if sort_by:
            _require_column(df, sort_by, "a sort column", view)
            sort_series = df[sort_by]
            if sort_by not in result.columns:
                result = result.assign(**{sort_by: sort_series})
            result = result.sort_values(sort_by, ascending=not sort_desc)
        result = result.reset_index(drop=True)
        if limit:
            result = result.head(limit)
        label_columns = list(result.columns)
        resolved_measures: list[dict[str, str]] = []
    else:
        resolved_measures = _normalise_measures(measures, view, df)
        if not resolved_measures:
            resolved_measures = [
                {"column": DEFAULT_MEASURE[view], "aggregation": "sum",
                 "label": DEFAULT_MEASURE[view]}
            ]

        group_by = [g for g in (group_by or []) if g]
        for col in group_by:
            _require_column(df, col, "a grouping", view)

        if time_bucket:
            df, bucket_label = _apply_time_bucket(df, time_bucket, view)
            group_by = [bucket_label] + group_by

        for m in resolved_measures:
            if m["aggregation"] != "count":
                df = _as_numeric(df, m["column"])

        if df.empty:
            result = pd.DataFrame()
        elif group_by:
            frames = []
            for m in resolved_measures:
                if m["aggregation"] == "count":
                    part = df.groupby(group_by, dropna=False).size().rename("count")
                else:
                    part = (df.groupby(group_by, dropna=False)[m["column"]]
                            .agg(m["aggregation"]).rename(m["label"]))
                frames.append(part)
            result = pd.concat(frames, axis=1).reset_index()
            measure_columns = [m["label"] for m in resolved_measures]
            label_columns = group_by

            # When one measure is split by a small categorical (DR vs CR, say),
            # pivot it into columns. "Amount by sub branch and Dr/Cr mark" then
            # reads as one row per sub branch with a DR and a CR column, which
            # is what a comparison should look like.
            pivoted = False
            if (len(group_by) == 2 and len(measure_columns) == 1
                    and result[group_by[1]].nunique() <= 4):
                wide = result.pivot(index=group_by[0], columns=group_by[1],
                                    values=measure_columns[0])
                wide.columns = [str(c) for c in wide.columns]
                result = wide.reset_index().fillna(0)
                measure_columns = [c for c in result.columns if c != group_by[0]]
                label_columns = [group_by[0]]
                pivoted = True

            # Sort by the named column if given, else the first measure.
            order_col = sort_by if sort_by in result.columns else measure_columns[0]
            result = result.sort_values(order_col, ascending=not sort_desc).reset_index(drop=True)
            if limit:
                result = result.head(limit)
            if pivoted:
                measures_note = f"split by {group_by[1]}"
            else:
                measures_note = None
        else:
            row = {}
            for m in resolved_measures:
                if m["aggregation"] == "count":
                    row["count"] = len(df)
                else:
                    value = getattr(df[m["column"]], m["aggregation"])()
                    row[m["label"]] = value.item() if hasattr(value, "item") else value
            result = pd.DataFrame([row])
            measure_columns = list(result.columns)

    primary = resolved_measures[0] if mode == "aggregate" and resolved_measures else None
    return {
        "view": view,
        "sheet": VIEW_SHEETS[view],
        "table": result,
        "mode": mode,
        "label_columns": label_columns,
        "measure_columns": measure_columns,
        "provenance": {
            "view": view,
            "sheet": VIEW_SHEETS[view],
            "source_file": DATA_FILE.name,
            "mode": mode,
            "filters": filters,
            "group_by": label_columns if mode == "aggregate" else [],
            "time_bucket": time_bucket,
            "measures": ([f"{m['aggregation']} of {m['column'] or 'rows'}"
                          for m in resolved_measures]
                         + ([measures_note] if mode == "aggregate" and measures_note else [])
                         ) if mode == "aggregate" else [],
            "columns": label_columns if mode == "list" else [],
            "sort_by": sort_by,
            "limit": limit,
            # Kept for existing readers of the provenance block.
            "measure": primary["column"] if primary else None,
            "aggregation": primary["aggregation"] if primary else None,
            "rows_in_view": total_rows,
            "rows_after_filters": rows_after_filters,
            "rows_returned": len(result),
            "executed_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    }


def sample_rows(view: str, n: int = 5) -> list[dict[str, Any]]:
    return get_frame(view).head(n).to_dict(orient="records")
