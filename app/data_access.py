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


def run_query(
    view: str,
    filters: list[dict[str, Any]] | None = None,
    group_by: list[str] | None = None,
    measure: str | None = None,
    aggregation: str = "sum",
    sort_desc: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run a deterministic aggregation and return both the result table and a
    provenance record describing exactly how it was produced."""
    df = get_frame(view)
    total_rows = len(df)

    filters = filters or []
    for spec in filters:
        df = _apply_filter(df, spec)
    rows_after_filters = len(df)

    group_by = [g for g in (group_by or []) if g]
    for col in group_by:
        if col not in df.columns:
            raise QueryError(
                f"Cannot group by '{col}' - no such column. "
                f"Available columns: {', '.join(get_frame(view).columns)}."
            )

    aggregation = (aggregation or "sum").lower()
    if aggregation not in {"sum", "mean", "count", "min", "max"}:
        raise QueryError(f"Unsupported aggregation '{aggregation}'.")

    if aggregation != "count":
        measure = measure or DEFAULT_MEASURE[view]
        if measure not in df.columns:
            raise QueryError(
                f"Measure '{measure}' does not exist. "
                f"Available columns: {', '.join(get_frame(view).columns)}."
            )
        if not pd.api.types.is_numeric_dtype(df[measure]):
            coerced = pd.to_numeric(df[measure], errors="coerce")
            if coerced.notna().sum() == 0:
                raise QueryError(f"Column '{measure}' is not numeric, so it cannot be aggregated.")
            df = df.assign(**{measure: coerced})
    else:
        measure = None

    if df.empty:
        result = pd.DataFrame()
    elif group_by:
        if aggregation == "count":
            result = df.groupby(group_by, dropna=False).size().reset_index(name="count")
            value_col = "count"
        else:
            result = (
                df.groupby(group_by, dropna=False)[measure]
                .agg(aggregation)
                .reset_index()
            )
            value_col = measure
        result = result.sort_values(value_col, ascending=not sort_desc).reset_index(drop=True)
        if limit:
            result = result.head(limit)
    else:
        # No grouping: return a single-row scalar result. Convert numpy scalars
        # to plain Python numbers so downstream type checks behave.
        value = len(df) if aggregation == "count" else getattr(df[measure], aggregation)()
        value = value.item() if hasattr(value, "item") else value
        result = pd.DataFrame([{measure or "count": value}])

    return {
        "view": view,
        "sheet": VIEW_SHEETS[view],
        "table": result,
        "provenance": {
            "view": view,
            "sheet": VIEW_SHEETS[view],
            "source_file": DATA_FILE.name,
            "filters": filters,
            "group_by": group_by,
            "measure": measure,
            "aggregation": aggregation,
            "rows_in_view": total_rows,
            "rows_after_filters": rows_after_filters,
            "rows_returned": len(result),
            "executed_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    }


def sample_rows(view: str, n: int = 5) -> list[dict[str, Any]]:
    return get_frame(view).head(n).to_dict(orient="records")
