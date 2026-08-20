"""Build the static showcase that GitHub Pages publishes.

Pages serves files, not code. Everything that needs the query engine or an API
key is therefore rendered here, once, and shipped as HTML - and that is the
whole safety argument for publishing this way: the published site holds no key
to steal and exposes no endpoint to call, so nobody can spend anything on our
behalf by opening the link.

What it cannot do is answer a new question. The views below are the curated set,
re-run against the data at build time; asking something new still needs the live
application.

Run: .venv\\Scripts\\python.exe build_static.py
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import auth, data_access, main, orchestrator

ROOT = Path(__file__).parent
OUT = ROOT / "docs"
STATIC = ROOT / "static"

client = TestClient(main.app)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "view"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------

def page(title: str, body: str, *, depth: int = 0, scripts: str = "") -> str:
    """One HTML document sharing the application's stylesheet."""
    up = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{up}styles.css">
<link rel="stylesheet" href="{up}showcase.css">
</head>
<body>
<div class="bg-orbs" aria-hidden="true">
  <div class="bg-orb orb-1"></div>
  <div class="bg-orb orb-2"></div>
  <div class="bg-orb orb-3"></div>
</div>
{body}
{scripts}
</body>
</html>
"""


def header(subtitle: str, *, depth: int = 0, crumb: str = "") -> str:
    up = "../" * depth
    back = (f'<a class="crumb-back" href="{up}index.html">&larr; All views</a>'
            if crumb else "")
    return f"""<header>
  <div>
    <div class="eyebrow">Intraday Liquidity</div>
    <h1>MI Report <em>Copilot</em></h1>
    <p class="sub">{subtitle}</p>
  </div>
  <div class="header-actions">
    {back}
    <a class="switch-btn" href="{up}index.html">Views</a>
    <a class="switch-btn" href="{up}model.html">Data model</a>
  </div>
</header>"""


def table_html(table: dict) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in table["columns"])
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
        for row in table["rows"]
    )
    note = ""
    if table.get("truncated"):
        note = (f'<p class="hint">Showing {len(table["rows"])} of '
                f'{table["total_rows"]:,} rows, as the tool does. '
                "The Excel download carries the rest.</p>")
    return (f'<div class="tablewrap extract"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>{note}")


def bullets(label: str, items: list) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        if isinstance(item, dict):
            text = " - ".join(str(v) for v in item.values() if v)
        else:
            text = str(item)
        rows.append(f"<li>{esc(text)}</li>")
    return (f'<div class="showcase-block"><h3>{esc(label)}</h3>'
            f'<ul class="showcase-list">{"".join(rows)}</ul></div>')


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build_view(view: dict, assets: Path) -> dict | None:
    """Re-run one saved view and write its page. Returns gallery metadata."""
    name = view["name"]
    r = client.post("/api/views/load", json={"view_id": view["id"]})
    if r.status_code != 200:
        print(f"    SKIPPED - {r.json().get('detail', '')[:110]}")
        return None
    report = r.json()

    exported = client.post("/api/export", json={"session_id": report["session_id"]})
    files: dict[str, str] = exported.json() if exported.status_code == 200 else {}

    key = slug(name)
    downloads = []
    for kind, label in (("pdf", "PDF"), ("markdown", "Markdown"),
                        ("excel", "Excel"), ("svg", "Chart (SVG)")):
        filename = files.get(kind)
        if not filename:
            continue
        source = orchestrator.EXPORT_DIR / filename
        if not source.exists():
            continue
        target = assets / f"{key}{source.suffix}"
        shutil.copy2(source, target)
        downloads.append(f'<a class="dl" href="../assets/{target.name}" download>{label}</a>')

    # The chart shown is the dark one the browser gets, so the page looks like
    # the tool rather than like the printout.
    chart = ""
    chart_url = report.get("chart_url") or ""
    if chart_url:
        source = orchestrator.EXPORT_DIR / Path(chart_url).name
        if source.exists():
            target = assets / f"{key}-chart{source.suffix}"
            shutil.copy2(source, target)
            chart = (f'<div class="chart-wrap">'
                     f'<img src="../assets/{target.name}" alt="{esc(report["title"])}">'
                     f"</div>")

    hero = ""
    for item in report.get("headline") or []:
        if isinstance(item, dict):
            hero += (f'<div class="hero"><div class="hero-label">'
                     f'{esc(item.get("label"))}</div>'
                     f'<div class="hero-value">{esc(item.get("value"))}</div>'
                     f'<div class="hero-note">{esc(item.get("note"))}</div></div>')

    body = f"""{header("A published snapshot. Figures were computed at build time.",
                        depth=1, crumb=name)}
<main class="showcase">
  <div class="panel">
    <div class="who">Report</div>
    <h2>{esc(report["title"])}</h2>
    <p class="showcase-desc">{esc(view.get("description"))}</p>
    <p class="showcase-ask"><span>Asked as</span> {esc(view.get("user_query"))}</p>
    {f'<div class="hero-row">{hero}</div>' if hero else ""}
    {chart}
    {f'<p class="hint">{esc(" ".join(report.get("chart_notes") or []))}</p>'
      if report.get("chart_notes") else ""}
    {f'<div class="showcase-block"><h3>Commentary</h3><p>{esc(report.get("narrative"))}</p></div>'
      if report.get("narrative") else ""}
    {table_html(report["table"])}
    {bullets("Limitations of this view", report.get("limitations") or [])}
    {bullets("What this view cannot tell you", report.get("unavailable") or [])}
    {bullets("Dependencies", report.get("dependencies") or [])}
    <div class="showcase-block"><h3>Downloads</h3>
      <div class="dl-row">{"".join(downloads) or "<span class='hint'>none</span>"}</div>
      <p class="hint">The Markdown documents everything considered, so a reader
         can hand it to an AI and ask follow-up questions about this view.</p>
    </div>
    <details class="prov-details"><summary>How this was produced</summary>
      <pre class="prov-json">{esc(json.dumps(report.get("provenance"), indent=2, default=str))}</pre>
    </details>
  </div>
</main>"""

    (OUT / "views" / f"{key}.html").write_text(
        page(f"{name} - MI Report Copilot", body, depth=1), encoding="utf-8")

    return {
        "name": name,
        "slug": key,
        "description": view.get("description", ""),
        "view": report["provenance"].get("sheet", view.get("view", "")),
        "rows": report["table"].get("total_rows", 0),
        "has_chart": bool(chart),
    }


def build_model() -> int:
    """Ship the data model as files, so the same view works with no server."""
    target = OUT / "model"
    overview = client.get("/api/model").json()
    (target / "index.json").write_text(json.dumps(overview), encoding="utf-8")
    for entry in overview["tables"]:
        detail = client.get(f"/api/model/{entry['name']}").json()
        (target / f"{slug(entry['name'])}.json").write_text(
            json.dumps(detail), encoding="utf-8")
    return len(overview["tables"])


def build_index(cards: list[dict], tables: int) -> None:
    items = "".join(f"""
      <a class="gcard" href="views/{c['slug']}.html" data-name="{esc(c['name'].lower())}">
        <h3>{esc(c['name'])}</h3>
        <p>{esc(c['description'])}</p>
        <div class="gcard-meta">
          <span>{esc(c['view'])}</span>
          <span>{c['rows']:,} rows</span>
          {"<span>chart</span>" if c["has_chart"] else ""}
        </div>
      </a>""" for c in cards)

    meta = data_access.get_metadata()
    body = f"""{header("Management views over intraday liquidity data, published as a "
                        "static snapshot.")}
<main class="showcase">
  <div class="panel notice">
    <h3>What this is</h3>
    <p>Each view below was produced by the tool and re-run against the data when
       this page was built. Every figure comes from the same deterministic
       aggregation the live application uses - none of it is written by a
       language model, which only names the query and comments on the result.</p>
    <p>This snapshot holds no API key and calls no service, so it cannot answer a
       new question. Asking your own needs the live application; these are the
       curated views plus the full data model behind them.</p>
    <p class="hint">Data classification: {esc(meta['data_classification'])}.
       All identifiers, counterparties, users and amounts are fabricated.</p>
  </div>

  <div class="gbar">
    <h2>{len(cards)} views</h2>
    <input type="search" id="gsearch" placeholder="Filter views&hellip;"
           autocomplete="off" aria-label="Filter views">
    <a class="switch-btn" href="model.html">Data model &mdash; {tables} tables</a>
  </div>
  <div class="ggrid" id="ggrid">{items}</div>
  <p class="model-foot">Built {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC
     from {esc(data_access.DATA_FILE.name)}.</p>
</main>"""

    search = """<script>
const gsearch = document.getElementById('gsearch');
gsearch.addEventListener('input', () => {
  const term = gsearch.value.trim().toLowerCase();
  document.querySelectorAll('.gcard').forEach((card) => {
    card.hidden = Boolean(term) && !card.dataset.name.includes(term);
  });
});
</script>"""
    (OUT / "index.html").write_text(page("MI Report Copilot", body, scripts=search),
                                    encoding="utf-8")


def build_model_page(tables: int) -> None:
    body = f"""{header("The tables behind the views, and the columns that join them.")}
<section id="modelView" class="model-view">
  <div class="model-bar">
    <button id="modelBack" class="secondary" hidden>&larr; All tables</button>
    <div class="model-crumb" id="modelCrumb">Data architecture</div>
    <input type="search" id="modelSearch" placeholder="Find a table&hellip;"
           autocomplete="off" aria-label="Find a table">
  </div>
  <div id="modelOverview" class="model-overview">loading&hellip;</div>
  <div id="modelDetail" class="model-detail" hidden></div>
</section>"""
    scripts = """<script>
/* The same view as the application, reading the payloads as files. */
window.MODEL_SOURCE = {
  overview: 'model/index.json',
  table: (name) => 'model/' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.json',
};
</script>
<script src="model.js"></script>"""
    (OUT / "model.html").write_text(
        page("Data model - MI Report Copilot", body, scripts=scripts), encoding="utf-8")


# --------------------------------------------------------------------------
# Safety check
# --------------------------------------------------------------------------

def audit(paths: list[Path]) -> list[str]:
    """Scan the built site for real values that escaped anonymisation.

    The published site is the one place a leak cannot be taken back, and the
    generator has reintroduced a real value before now. The mapping lives
    outside the repository, so this runs from a working copy or not at all.
    """
    try:
        import anonymise
    except ImportError:
        return ["anonymise.py not importable - audit skipped"]
    try:
        mapping = anonymise._load()
    except Exception as exc:  # noqa: BLE001 - a missing mapping must not be fatal
        return [f"golden source unavailable ({exc}) - audit skipped"]

    sensitive = {"person", "venue", "counterparty", "legal_entity", "system",
                 "ledger_account", "org_unit", "account_number"}
    reals = {}
    for category, table in mapping["categories"].items():
        if category not in sensitive:
            continue
        for real, fake in table.items():
            # A value mapped to itself is generic vocabulary, not a disclosure.
            if real != fake and len(real) >= 5 and not real.isdigit():
                reals[real.casefold()] = category

    leaks = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for real, category in reals.items():
            if real in text:
                leaks.append(f"{path.relative_to(OUT)}: '{real}' [{category}]")
    return leaks


def main_build() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("", "views", "assets", "model"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    # Pages runs Jekyll unless told otherwise, and Jekyll drops files that begin
    # with an underscore.
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    signin = client.post("/api/login", json={"password": auth.password()})
    if signin.status_code != 200:
        print("Could not sign in to the application.")
        return 1
    if not client.get("/api/health").json()["api_key_configured"]:
        print("No usable API key configured - the narratives need one.")
        return 1

    shutil.copy2(STATIC / "styles.css", OUT / "styles.css")
    shutil.copy2(STATIC / "model.js", OUT / "model.js")
    shutil.copy2(ROOT / "showcase.css", OUT / "showcase.css")

    views = client.get("/api/views").json()["views"]
    print(f"Building {len(views)} views\n")
    cards = []
    for view in views:
        print(f"  {view['name']}")
        card = build_view(view, OUT / "assets")
        if card:
            cards.append(card)
            print(f"    {card['rows']:,} rows"
                  + (", chart" if card["has_chart"] else ", table only"))

    tables = build_model()
    build_model_page(tables)
    build_index(cards, tables)

    text_files = [p for p in OUT.rglob("*")
                  if p.suffix in {".html", ".json", ".md", ".svg"}]
    leaks = audit(text_files)

    print(f"\n{len(cards)}/{len(views)} views, {tables} model tables, "
          f"{len(text_files)} text files.")
    if leaks:
        print("\nANONYMISATION AUDIT FAILED - not fit to publish:")
        for leak in leaks[:20]:
            print(f"  {leak}")
        return 1
    print("Anonymisation audit clean.")
    print(f"Output: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main_build())
