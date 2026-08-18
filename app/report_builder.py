"""Chart rendering for MI reports.

Design decisions follow DESIGN.md:

- Every chart here plots ONE measure, so it is a single-series chart: one hue,
  no legend, and no value-ramp across nominal categories.
- A scalar result renders as a hero number, not a one-bar bar chart.
- There is no pie option. For part-to-whole a horizontal bar is used instead:
  bars compare magnitudes more accurately, and a multi-colour pie could not clear
  the colour-separation gate for colour-vision deficiency at six segments.
- Two themes, each validated against the surface it renders on. The web UI is
  dark; the PDF is light, because a dark report wastes ink and reads wrong when
  printed. Dark is a selected theme, not an inversion.
- Grid and axes are recessive hairlines; marks are thin; values are direct-labelled
  because a PDF has no tooltip to fall back on.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: no display required

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

THEMES: dict[str, dict[str, Any]] = {
    "dark": {
        "surface": "#12121F",
        "series": "#A100FF",
        # Multi-measure comparisons only. Validated as a set against this
        # surface: contrast, lightness band, and colour-vision separation.
        "series_set": ["#A100FF", "#199E70", "#D95926"],
        "ink_primary": "#FFFFFF",
        "ink_secondary": "#B9B6C9",
        "ink_muted": "#8A8899",
        "gridline": "#252336",
        "baseline": "#3A3750",
    },
    "light": {
        "surface": "#FCFCFB",
        "series": "#8A15E0",
        "series_set": ["#8A15E0", "#1BAF7A", "#EB6834"],
        "ink_primary": "#0B0B0B",
        "ink_secondary": "#52514E",
        "ink_muted": "#898781",
        "gridline": "#E1E0D9",
        "baseline": "#C3C2B7",
    },
}
DEFAULT_THEME = "dark"

FONT_STACK = ["Segoe UI", "DejaVu Sans", "sans-serif"]
MAX_CATEGORIES = 15  # beyond this the tail folds into "Other"


def _theme(name: str | None) -> dict[str, str]:
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def _human_number(value: float) -> str:
    """Compact axis/label form: 1.2bn, 340.5m, 12.3k."""
    abs_v = abs(value)
    for cutoff, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs_v >= cutoff:
            return f"{value / cutoff:,.1f}{suffix}"
    if abs_v >= 1:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _style_axes(ax, t: dict[str, str], horizontal: bool) -> None:
    ax.set_facecolor(t["surface"])
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(t["baseline"])
        ax.spines[spine].set_linewidth(1.0)
    # Solid hairline grid on the value axis only - never dashed.
    ax.grid(axis="x" if horizontal else "y", color=t["gridline"], linewidth=1.0, alpha=1.0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=t["ink_muted"], labelsize=9, length=0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_color(t["ink_secondary"])


def _fold_tail(df: pd.DataFrame, label_col: str,
               value_cols: list[str]) -> tuple[pd.DataFrame, bool]:
    """Keep the largest categories; fold the remainder into a single 'Other' row
    rather than rendering an unreadable number of marks."""
    if len(df) <= MAX_CATEGORIES:
        return df, False
    head = df.head(MAX_CATEGORIES - 1).copy()
    tail = df.iloc[MAX_CATEGORIES - 1 :]
    other = {label_col: f"Other ({len(df) - MAX_CATEGORIES + 1})"}
    for col in value_cols:
        other[col] = tail[col].sum()
    return pd.concat([head, pd.DataFrame([other])], ignore_index=True), True


def _render_hero(value: float, caption: str, out_path: Path, t: dict[str, str]) -> None:
    """A single number is a stat tile, not a chart."""
    fig, ax = plt.subplots(figsize=(9, 2.6), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    ax.set_facecolor(t["surface"])
    ax.axis("off")
    # Counts read as whole numbers; money keeps two decimals.
    text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    ax.text(0.5, 0.62, text, ha="center", va="center",
            fontsize=46, color=t["ink_primary"], fontfamily=FONT_STACK)
    ax.text(0.5, 0.18, caption, ha="center", va="center",
            fontsize=11, color=t["ink_secondary"], fontfamily=FONT_STACK)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=t["surface"], bbox_inches="tight")
    plt.close(fig)


def build_chart(
    table: pd.DataFrame,
    chart_type: str,
    title: str,
    out_dir: Path,
    session_id: str,
    theme: str | None = None,
    measure_columns: list[str] | None = None,
    label_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Render the chart and return its path plus notes about what was done.

    Returns chart_path=None for table-only output.
    """
    t = _theme(theme)
    plt.rcParams["font.family"] = FONT_STACK
    out_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision: a refine can rebuild within the same second, and a
    # second-precision name would silently overwrite the previous chart.
    stamp = dt.datetime.now().strftime("%H%M%S%f")
    suffix = f"_{theme}" if theme and theme != DEFAULT_THEME else ""
    out_path = out_dir / f"chart_{session_id}_{stamp}{suffix}.png"
    notes: list[str] = []

    if table.empty:
        return {"chart_path": None, "chart_type": "none",
                "notes": ["No rows matched the query, so no chart was produced."]}

    # A scalar result (single row, single column) is a hero number.
    if table.shape == (1, 1):
        value = table.iloc[0, 0]
        # pd.api.types.is_number covers numpy scalars, which do not subclass int.
        if pd.api.types.is_number(value):
            _render_hero(float(value), title, out_path, t)
            return {"chart_path": out_path, "chart_type": "stat",
                    "notes": ["Rendered as a single headline figure rather than a one-bar chart."]}

    if chart_type == "table" or table.shape[1] < 2:
        return {"chart_path": None, "chart_type": "table",
                "notes": ["Presented as a table; no chart was requested."]}

    # The query engine names which columns are measures; fall back to the
    # last-column convention when it does not.
    measures = [c for c in (measure_columns or []) if c in table.columns]
    if not measures:
        measures = [table.columns[-1]]
    label_cols = [c for c in (label_columns or list(table.columns[:-1]))
                  if c in table.columns and c not in measures]
    if not label_cols:
        label_cols = [c for c in table.columns if c not in measures][:1]
    if not label_cols:
        return {"chart_path": None, "chart_type": "table",
                "notes": ["No grouping column to plot against; shown as a table."]}

    value_col = measures[0]
    plot_df = table.copy()
    if len(label_cols) > 1:
        plot_df["__label__"] = plot_df[label_cols].astype(str).agg(" / ".join, axis=1)
        label_col = "__label__"
    else:
        label_col = label_cols[0]
        plot_df[label_col] = plot_df[label_col].astype(str)

    numeric_measures = [m for m in measures if pd.api.types.is_numeric_dtype(plot_df[m])]
    if not numeric_measures:
        return {"chart_path": None, "chart_type": "table",
                "notes": [f"Column '{value_col}' is not numeric, so the result is shown as a table."]}
    measures = numeric_measures
    value_col = measures[0]

    # A time series must never be folded into an "Other" bucket - the x axis is
    # chronological, so the tail is "later", not "smaller".
    if chart_type == "line":
        if len(plot_df) > 40:
            notes.append(
                f"{len(plot_df)} time buckets are plotted, which is dense to read. "
                "A coarser granularity (day rather than hour) would show the shape "
                "more clearly."
            )
    else:
        plot_df, folded = _fold_tail(plot_df, label_col, measures)
        if folded:
            notes.append(
                f"Only the largest {MAX_CATEGORIES - 1} categories are charted; the remainder "
                "are combined into a single 'Other' bar. The full breakdown is in the table."
            )

    # More than one measure is a comparison: grouped bars with a legend.
    if len(measures) > 1:
        if len(measures) > len(t["series_set"]):
            kept = len(t["series_set"])
            notes.append(
                f"Only the first {kept} measures are charted; plotting more would need "
                "colours that cannot be told apart reliably. The table shows all of them."
            )
            measures = measures[:kept]
        fig = _render_grouped(plot_df, label_col, measures, title, t,
                              horizontal=(chart_type == "barh"))
    elif chart_type == "line":
        fig = _render_line(plot_df, label_col, value_col, title, t)
    elif chart_type == "barh":
        fig = _render_barh(plot_df, label_col, value_col, title, t)
    else:
        fig = _render_bar(plot_df, label_col, value_col, title, t)

    fig.savefig(out_path, facecolor=t["surface"], bbox_inches="tight")
    plt.close(fig)
    return {"chart_path": out_path, "chart_type": chart_type, "notes": notes}


def _render_bar(df, label_col: str, value_col: str, title: str, t: dict[str, str]):
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    # Single series -> one hue for every bar. Never a value-ramp on categories.
    bars = ax.bar(df[label_col], df[value_col], color=t["series"], width=0.62)
    _style_axes(ax, t, horizontal=False)
    ax.set_title(title, fontsize=12, color=t["ink_primary"], loc="left", pad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _human_number(v)))

    rotation = 30 if df[label_col].astype(str).str.len().max() > 8 else 0
    if rotation:
        plt.setp(ax.get_xticklabels(), rotation=rotation, ha="right")

    if len(df) <= 12:  # direct-label only while the labels still fit
        span = max(abs(df[value_col].max()), abs(df[value_col].min()), 1)
        for bar, value in zip(bars, df[value_col]):
            offset = span * 0.015
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + (offset if value >= 0 else -offset),
                    _human_number(value), ha="center",
                    va="bottom" if value >= 0 else "top",
                    fontsize=8.5, color=t["ink_secondary"])
    fig.tight_layout()
    return fig


def _render_barh(df, label_col: str, value_col: str, title: str, t: dict[str, str]):
    # Largest at the top reads most naturally.
    df = df.iloc[::-1].reset_index(drop=True)
    height = max(3.0, 0.42 * len(df) + 1.4)
    fig, ax = plt.subplots(figsize=(9, height), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    bars = ax.barh(df[label_col], df[value_col], color=t["series"], height=0.62)
    _style_axes(ax, t, horizontal=True)
    ax.set_title(title, fontsize=12, color=t["ink_primary"], loc="left", pad=12)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _human_number(v)))

    span = max(abs(df[value_col].max()), abs(df[value_col].min()), 1)
    for bar, value in zip(bars, df[value_col]):
        offset = span * 0.012
        ax.text(bar.get_width() + (offset if value >= 0 else -offset),
                bar.get_y() + bar.get_height() / 2,
                _human_number(value), va="center",
                ha="left" if value >= 0 else "right",
                fontsize=8.5, color=t["ink_secondary"])
    # Headroom on whichever side carries labels, so a direct label is never
    # clipped by the axis or overlapped by the category names on the left.
    lo, hi = ax.get_xlim()
    pad = (hi - lo) * 0.14
    has_negative = bool((df[value_col] < 0).any())
    ax.set_xlim(lo - (pad if has_negative else 0), hi + pad)
    fig.tight_layout()
    return fig


def _render_grouped(df, label_col: str, measures: list[str], title: str,
                    t: dict[str, Any], horizontal: bool):
    """Grouped bars for a comparison of two or three measures.

    Identity is never colour-alone: a legend is always present, and the 2px
    surface gap between adjacent bars keeps them separable.
    """
    n = len(measures)
    count = len(df)
    if horizontal:
        height = max(3.2, 0.30 * count * n + 1.6)
        fig, ax = plt.subplots(figsize=(9, height), dpi=200)
    else:
        fig, ax = plt.subplots(figsize=(9, 4.9), dpi=200)
    fig.patch.set_facecolor(t["surface"])

    span = 0.78
    width = span / n
    positions = range(count)

    for i, measure in enumerate(measures):
        offset = -span / 2 + width * (i + 0.5)
        colour = t["series_set"][i]
        locs = [p + offset for p in positions]
        if horizontal:
            ax.barh(locs, df[measure], height=width * 0.9, color=colour, label=measure)
        else:
            ax.bar(locs, df[measure], width=width * 0.9, color=colour, label=measure)

    if horizontal:
        ax.set_yticks(list(positions))
        ax.set_yticklabels(df[label_col].astype(str))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _human_number(v)))
    else:
        ax.set_xticks(list(positions))
        ax.set_xticklabels(df[label_col].astype(str))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _human_number(v)))
        if df[label_col].astype(str).str.len().max() > 8:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    _style_axes(ax, t, horizontal=horizontal)
    ax.set_title(title, fontsize=12, color=t["ink_primary"], loc="left", pad=12)

    legend = ax.legend(frameon=False, fontsize=9, loc="upper right", ncol=min(n, 3))
    for text in legend.get_texts():
        text.set_color(t["ink_secondary"])

    fig.tight_layout()
    return fig


def _render_line(df, label_col: str, value_col: str, title: str, t: dict[str, str]):
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=200)
    fig.patch.set_facecolor(t["surface"])
    ax.plot(df[label_col], df[value_col], color=t["series"], linewidth=2.0,
            marker="o", markersize=5, markerfacecolor=t["series"],
            markeredgecolor=t["surface"], markeredgewidth=2)  # 2px surface ring
    _style_axes(ax, t, horizontal=False)
    ax.set_title(title, fontsize=12, color=t["ink_primary"], loc="left", pad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _human_number(v)))

    # Thin the tick labels rather than letting them collide on a dense series.
    if len(df) > 12:
        step = max(1, len(df) // 10)
        ax.set_xticks(range(0, len(df), step))
        ax.set_xticklabels(df[label_col].astype(str).iloc[::step])
    if df[label_col].astype(str).str.len().max() > 8:
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    # Selective direct labels: first and last point only, never every point.
    for idx in {0, len(df) - 1}:
        ax.annotate(_human_number(df[value_col].iloc[idx]),
                    (idx, df[value_col].iloc[idx]),
                    textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8.5, color=t["ink_secondary"])
    fig.tight_layout()
    return fig


def format_table_for_display(table: pd.DataFrame, max_rows: int = 60) -> dict[str, Any]:
    """Serialise the result table for the browser and the PDF."""
    display = table.head(max_rows).copy()
    for col in display.columns:
        if pd.api.types.is_numeric_dtype(display[col]):
            display[col] = display[col].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "")
        else:
            display[col] = display[col].map(lambda v: "" if pd.isna(v) else str(v))
    return {
        "columns": list(display.columns),
        "rows": display.values.tolist(),
        "truncated": len(table) > max_rows,
        "total_rows": len(table),
    }
