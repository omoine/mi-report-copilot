"""The data model behind the views: what each table holds and how they connect.

Built from reference_model.py - the definition the reference data was generated
from - rather than inferred by matching column names. A link shown here is one
the query engine can actually make, which is the whole point of showing it: a
reader who can see that Counterparty reaches counterparty_master knows a
question about a counterparty's country is answerable before they ask it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pandas as pd

from . import data_access

try:  # the generator script lives at the repository root
    import reference_model
except ImportError:  # pragma: no cover - the app still runs without it
    reference_model = None  # type: ignore[assignment]


# Verified against the data rather than assumed: client is unique on
# (Account, Value Date), business_ledger on Transaction Reference, and
# nostro_transfer on Reference.
VIEW_GRAIN = {
    "nostro_transfer": "one row per nostro transfer instruction",
    "client": "one row per account per value date, with its reconciliation",
    "business_ledger": "one row per ledger transaction",
}

SAMPLE_ROWS = 10

# Enough to show what a column holds without pasting an identifier column into
# the page. Columns with more than this report their count and a sample.
MAX_DISTINCT_SHOWN = 60


def _specs() -> dict[str, dict[str, Any]]:
    if reference_model is None:
        return {}
    return {**reference_model.ROUND_1, **reference_model.ROUND_2}


def _key_column(table: str) -> str | None:
    """The column a reference table is joined on - always its first."""
    spec = _specs().get(table)
    if spec:
        return spec["key"][0]
    frame = data_access.reference_tables().get(table)
    if frame is not None and not frame.empty:
        return frame.columns[0]
    return None


@lru_cache(maxsize=1)
def _edges() -> list[dict[str, str]]:
    """Every declared foreign key, as from-table.column -> to-table.key."""
    edges: list[dict[str, str]] = []

    def add(from_table: str, from_column: str, to_table: str) -> None:
        to_column = _key_column(to_table)
        if to_column is None:
            return
        edge = {"from_table": from_table, "from_column": from_column,
                "to_table": to_table, "to_column": to_column}
        if edge not in edges:
            edges.append(edge)

    for table, spec in _specs().items():
        # Round 1 declares which live view column it hangs off.
        for view, column in spec.get("joins_from", []):
            add(view, column, table)
        # Round 2 declares the round-1 column that reaches it.
        for path in spec.get("reached_via", []):
            source, _, column = path.partition(".")
            if column:
                add(source, column, table)
        # Attributes marked fk: point at another table.
        for column, kind, _note in spec.get("attributes", []):
            if isinstance(kind, str) and kind.startswith("fk:"):
                add(table, column, kind.split(":", 1)[1])

    return edges


@lru_cache(maxsize=1)
def overview() -> dict[str, Any]:
    """Every table, its grain and row count, plus the links between them.

    Laid out in three layers because that is what the model actually is: the
    transaction views, the tables keyed directly off them, and the tables only
    reachable once a round-one lookup has brought their key across.
    """
    specs = _specs()
    tables: list[dict[str, Any]] = []

    for view, sheet in data_access.VIEW_SHEETS.items():
        frame = data_access.get_frame(view)
        tables.append({
            "name": view,
            "label": sheet,
            "kind": "view",
            "layer": 0,
            "domain": "Transaction data",
            "grain": VIEW_GRAIN.get(view, ""),
            "rows": len(frame),
            "column_count": len(frame.columns),
            "key": None,
        })

    for table, frame in data_access.reference_tables().items():
        spec = specs.get(table, {})
        layer = reference_model.round_of(table) if reference_model else 1
        tables.append({
            "name": table,
            "label": table,
            "kind": "reference",
            "layer": layer,
            "domain": spec.get("domain", "Reference data"),
            "grain": spec.get("grain", ""),
            "rows": len(frame),
            "column_count": len(frame.columns),
            "key": _key_column(table),
        })

    return {
        "tables": tables,
        "links": _edges(),
        "layers": ["Transaction views", "Keyed off the views",
                   "Reached through a lookup"],
        "data_classification": data_access.get_metadata()["data_classification"],
    }


def _definitions(view_label: str) -> dict[str, str]:
    """Column definitions from the workbook's own data dictionary."""
    out: dict[str, str] = {}
    for entry in data_access.get_metadata()["data_dictionary"]:
        if entry.get("View") != view_label:
            continue
        column = entry.get("Column Name")
        text = entry.get("Definition / Business Rule")
        if column and text:
            out[str(column)] = str(text)
    return out


def _notes(table: str) -> dict[str, str]:
    """Hand-written notes on reference columns, where the model records one."""
    spec = _specs().get(table, {})
    return {column: note
            for column, _kind, note in spec.get("attributes", [])
            if note}


def _column_values(series: pd.Series) -> dict[str, Any]:
    """What a column actually holds - the distinct values, or a range."""
    clean = series.dropna()
    distinct = clean.unique()
    info: dict[str, Any] = {"distinct": int(len(distinct)),
                            "missing": int(series.isna().sum())}

    if pd.api.types.is_numeric_dtype(series) and len(clean):
        info["kind"] = "number"
        info["min"] = float(clean.min())
        info["max"] = float(clean.max())
        info["values"] = [str(v) for v in distinct[:12]]
        return info

    if pd.api.types.is_datetime64_any_dtype(series) and len(clean):
        info["kind"] = "date"
        info["min"] = str(clean.min())
        info["max"] = str(clean.max())
        info["values"] = [str(v) for v in distinct[:12]]
        return info

    info["kind"] = "text"
    shown = [str(v) for v in distinct[:MAX_DISTINCT_SHOWN]]
    info["values"] = shown
    info["truncated"] = len(distinct) > len(shown)
    return info


def table_detail(name: str) -> dict[str, Any]:
    """One table: an extract, what every column holds, and where it links."""
    if name in data_access.VIEW_SHEETS:
        frame = data_access.get_frame(name)
        label = data_access.VIEW_SHEETS[name]
        kind, grain = "view", VIEW_GRAIN.get(name, "")
        definitions, notes = _definitions(data_access.DICTIONARY_LABELS[name]), {}
        domain = "Transaction data"
    else:
        frames = data_access.reference_tables()
        if name not in frames:
            raise KeyError(f"No table called '{name}'.")
        frame = frames[name]
        label, kind = name, "reference"
        spec = _specs().get(name, {})
        grain = spec.get("grain", "")
        domain = spec.get("domain", "Reference data")
        definitions, notes = {}, _notes(name)

    edges = _edges()
    columns = []
    for column in frame.columns:
        links_out = [e for e in edges
                     if e["from_table"] == name and e["from_column"] == column]
        columns.append({
            "name": column,
            "dtype": data_access._friendly_dtype(frame[column].dropna()),
            "description": definitions.get(column) or notes.get(column, ""),
            "is_key": column == _key_column(name) and kind == "reference",
            "links_to": [{"table": e["to_table"], "column": e["to_column"]}
                         for e in links_out],
            **_column_values(frame[column]),
        })

    # Where this table is reached FROM, so navigation works in both directions.
    links_in = [{"table": e["from_table"], "column": e["from_column"],
                 "onto": e["to_column"]}
                for e in edges if e["to_table"] == name]

    sample = frame.head(SAMPLE_ROWS).astype("string").fillna("").to_dict(orient="records")

    return {
        "name": name,
        "label": label,
        "kind": kind,
        "domain": domain,
        "grain": grain,
        "rows": len(frame),
        "key": _key_column(name) if kind == "reference" else None,
        "columns": columns,
        "links_in": links_in,
        "sample": sample,
        "sample_columns": list(frame.columns),
    }
