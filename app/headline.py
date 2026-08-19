"""The two or three numbers a report should lead with.

A manager reads the headline and stops; the breakdown is there to be
interrogated when the headline prompts a question. Every figure here is computed
from the result table, never written by the model.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Aggregations where a total across the groups is a real number. Summing
# averages, or summing a peak, is not.
TOTALLABLE = {"sum", "count"}


def _fmt(value: float) -> str:
    for cutoff, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= cutoff:
            return f"{value / cutoff:,.1f}{suffix}"
    return f"{value:,.0f}" if abs(value) >= 1 else f"{value:,.2f}"


def build(result: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, str]]:
    """Return a short list of {label, value, detail} for the top of a report."""
    table: pd.DataFrame = result.get("table")
    if table is None or table.empty:
        return []

    mode = result.get("mode", "aggregate")
    labels = [c for c in (result.get("label_columns") or []) if c in table.columns]
    measures = [c for c in (result.get("measure_columns") or []) if c in table.columns]
    numeric = [c for c in measures
               if pd.api.types.is_numeric_dtype(table[c]) and not c.startswith("% of")]

    if mode == "list":
        return _list_headline(table, provenance)
    if mode == "peak":
        return _peak_headline(table)
    if mode == "distribution":
        return _distribution_headline(table)
    return _aggregate_headline(table, labels, numeric, provenance)


def _list_headline(table: pd.DataFrame, provenance: dict[str, Any]) -> list[dict[str, str]]:
    out = [{"label": "Records", "value": f"{provenance.get('rows_after_filters', len(table)):,}",
            "detail": "matching the filters applied"}]
    numeric = [c for c in table.columns if pd.api.types.is_numeric_dtype(table[c])]
    money = [c for c in numeric if any(w in c for w in ("Amount", "Value", "Balance"))]
    if money:
        col = money[0]
        out.append({"label": f"Total {col}", "value": _fmt(table[col].sum()),
                    "detail": "across the records shown"})
        out.append({"label": "Largest", "value": _fmt(table[col].abs().max()),
                    "detail": f"single {col.lower()}"})
    return out[:3]


def _peak_headline(table: pd.DataFrame) -> list[dict[str, str]]:
    if "Largest usage" not in table.columns:
        return []
    worst = table.iloc[0]
    group_cols = [c for c in ("Value Date", "CCY (Local)", "Currency")
                  if c in table.columns]
    who = " ".join(str(worst[c]) for c in group_cols)
    out = [{"label": "Largest intraday usage", "value": _fmt(worst["Largest usage"]),
            "detail": f"{who} at {worst.get('Usage at', '')}"}]
    if "Peak position" in table.columns:
        out.append({"label": "Highest position", "value": _fmt(table["Peak position"].max()),
                    "detail": "best point reached on any day shown"})
    out.append({"label": "Positions shown", "value": f"{len(table):,}",
                "detail": "day and group combinations, worst first"})
    return out


def _distribution_headline(table: pd.DataFrame) -> list[dict[str, str]]:
    if "Statistic" not in table.columns or "Value" not in table.columns:
        return []
    stats = dict(zip(table["Statistic"], table["Value"]))
    out = []
    if "median" in stats:
        out.append({"label": "Median", "value": _fmt(stats["median"]),
                    "detail": "the typical case"})
    if "mean" in stats and "median" in stats and stats["median"]:
        ratio = stats["mean"] / stats["median"] if stats["median"] else 0
        out.append({"label": "Mean", "value": _fmt(stats["mean"]),
                    "detail": f"{ratio:.1f}x the median - the distribution is skewed"
                    if abs(ratio) > 1.3 else "close to the median"})
    if "p95" in stats:
        out.append({"label": "95th percentile", "value": _fmt(stats["p95"]),
                    "detail": "1 in 20 exceed this"})
    return out[:3]


def _aggregate_headline(table: pd.DataFrame, labels: list[str], numeric: list[str],
                        provenance: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not numeric:
        return [{"label": "Rows", "value": f"{len(table):,}", "detail": "in this breakdown"}]

    primary = numeric[0]
    aggs = provenance.get("measures") or []
    additive = any(a.split()[0] in TOTALLABLE for a in aggs) if aggs else True

    # The tail folded into "Other" is not a contributor; it is the absence of
    # one, and picking it as the largest would name a bucket rather than a thing.
    named = table
    if labels:
        named = table[~table[labels[0]].astype(str).str.startswith("Other (")]
    group_word = _plural(labels[0]) if labels else "groups"

    if additive:
        out.append({"label": f"Total {primary}", "value": _fmt(table[primary].sum()),
                    "detail": f"across {len(table):,} {group_word}"})

    # Debits and credits net against each other, so a share of the net total can
    # exceed 100% and means little. Gross flow is the honest denominator.
    values = table[primary]
    mixed_sign = bool((values > 0).any() and (values < 0).any())
    gross = values.abs().sum()

    if labels and len(named) > 1:
        top = named.loc[named[primary].abs().idxmax()]
        detail = f"{top[labels[0]]}"
        share_col = next((c for c in named.columns if c.startswith("% of")), None)
        if share_col is not None and pd.notna(top.get(share_col)) and not mixed_sign:
            detail += f" - {top[share_col]:.1f}% of the total"
        elif gross:
            basis = "gross flow" if mixed_sign else "the total"
            detail += f" - {100 * abs(top[primary]) / gross:.1f}% of {basis}"
        out.append({"label": "Largest", "value": _fmt(top[primary]), "detail": detail})

    # Concentration: how much sits in the top three, measured on gross flow so
    # that offsetting debits and credits do not disguise it.
    if additive and len(named) >= 4 and gross:
        ordered = named[primary].abs().sort_values(ascending=False)
        top3 = 100 * ordered.head(3).sum() / ordered.sum()
        basis = "gross flow" if mixed_sign else "the total"
        out.append({"label": "Concentration", "value": f"{top3:.0f}%",
                    "detail": f"of {basis} in the top 3 of {len(named):,}"})
    return out[:3]


def _plural(label: str) -> str:
    """Readable plural for a column name used as a noun."""
    word = label.lower()
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"
