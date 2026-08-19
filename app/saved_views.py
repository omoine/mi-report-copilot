"""Saved view configurations.

A saved view stores the *query specification*, never the results. Loading one
re-runs it against whatever the data currently holds, so a saved view is always
a live question rather than a stale answer - which is the point of MI.

Stored as JSON on disk so views survive a restart and are shared by everyone
using the same server.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

STORE = Path(
    os.getenv("SAVED_VIEWS_FILE")
    or Path(__file__).resolve().parent.parent / "saved_views.json"
)

MAX_NAME = 80


class SavedViewError(ValueError):
    """A user-facing problem with saving or loading a view."""


def _read() -> list[dict[str, Any]]:
    if not STORE.exists() or not STORE.stat().st_size:
        return []
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # A corrupt store must not take the app down; it is a convenience layer.
        return []
    return data if isinstance(data, list) else []


def _write(views: list[dict[str, Any]]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(views, indent=2, ensure_ascii=False), encoding="utf-8")


def list_views(search: str | None = None) -> list[dict[str, Any]]:
    """All saved views, newest first, optionally filtered by a search term."""
    views = sorted(_read(), key=lambda v: v.get("saved_at", ""), reverse=True)
    if search:
        needle = search.strip().casefold()
        views = [
            v for v in views
            if needle in v.get("name", "").casefold()
            or needle in v.get("description", "").casefold()
            or needle in v.get("user_query", "").casefold()
        ]
    # The full specification is not needed to render a list.
    return [
        {
            "id": v["id"],
            "name": v["name"],
            "description": v.get("description", ""),
            "user_query": v.get("user_query", ""),
            "view": v.get("query", {}).get("view", ""),
            "mode": v.get("query", {}).get("mode", ""),
            "saved_at": v.get("saved_at", ""),
        }
        for v in views
    ]


def get_view(view_id: str) -> dict[str, Any]:
    for view in _read():
        if view["id"] == view_id:
            return view
    raise SavedViewError("That saved view no longer exists.")


def save_view(
    name: str,
    query: dict[str, Any],
    chart_type: str,
    user_query: str,
    understood: str = "",
    limitations: list[str] | None = None,
    dependencies: list[str] | None = None,
    description: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Store a view under a name. Names are unique so the dropdown stays usable."""
    clean = re.sub(r"\s+", " ", (name or "").strip())
    if not clean:
        raise SavedViewError("Give the view a name.")
    if len(clean) > MAX_NAME:
        raise SavedViewError(f"Name is too long (max {MAX_NAME} characters).")
    if not query or not query.get("view"):
        raise SavedViewError("There is no view to save yet.")

    views = _read()
    existing = next((v for v in views if v["name"].casefold() == clean.casefold()), None)
    if existing and not overwrite:
        raise SavedViewError(
            f"A view called '{existing['name']}' already exists. "
            "Save it under a different name, or confirm replacing it."
        )

    record = {
        "id": existing["id"] if existing else uuid.uuid4().hex[:12],
        "name": clean,
        "description": description.strip()[:400],
        "user_query": user_query,
        "understood": understood,
        "query": query,
        "chart_type": chart_type,
        "limitations": limitations or [],
        "dependencies": dependencies or [],
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if existing:
        views = [record if v["id"] == existing["id"] else v for v in views]
    else:
        views.append(record)
    _write(views)
    return record


def delete_view(view_id: str) -> None:
    views = _read()
    remaining = [v for v in views if v["id"] != view_id]
    if len(remaining) == len(views):
        raise SavedViewError("That saved view no longer exists.")
    _write(remaining)
