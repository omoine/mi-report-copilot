# MI Report Copilot

A proof of concept for getting management information views on demand, without
waiting on a reporting team.

You describe the view you want in plain English. The assistant states what it
understood **and what the limitations are**, and builds nothing until you
confirm. You can then fine-tune the result and export it as a PDF, alongside a
Markdown companion documenting everything that went into it.

Built around intraday liquidity operations in a banking context. It runs on a
synthetic dataset included in `data/` — no production data, no real systems.

## The flow

```
ask  →  assistant restates + flags caveats  →  you confirm  →  report built
                                                                    ↓
                          PDF + Markdown  ←  export  ←  fine-tune (repeat)
```

## The idea

Three things make this different from asking a chatbot about a spreadsheet.

**The model never produces a figure.** It chooses *which* query to run and writes
the commentary. Every number comes from deterministic pandas aggregation, and
every report records the exact query, filters and row counts behind it. For
reconciliation and ledger data that is the difference between an auditable report
and a plausible-looking one.

**It says no.** If you ask for something the data cannot support — credit limits,
utilisation, ratings — it declines and names what is missing, rather than
building a similar-looking report from unrelated columns. That failure mode is
the dangerous one: a reader acts on a figure that does not mean what they think.

**Every report explains itself.** The exported Markdown carries the question, the
interpretation, the exact query, the figures, the caveats, the field definitions
and the full refinement history. Hand it to any AI assistant and it can answer
follow-up questions about the report accurately, without access to the system
that produced it.

## The dataset

The app loads `data/synthetic_liquidity_month.xlsx` when present: a generated
month of business days across ~6,000 account/currency pairs, ~900 of them active,
with an intraday arrival curve, month-end and day-of-week effects, and deliberate
anomalies. Regenerate or resize it with:

```bash
python generate_synthetic.py --accounts 6000 --active 900 --days 22
```

Set `DATA_FILE` to pin a different workbook. The original single-day sample is
kept as `data/synthetic_liquidity_views.xlsx` and is what the tests run against.

### The original sample

`data/synthetic_liquidity_views.xlsx` is entirely fabricated — all accounts,
counterparties, entities, users and amounts. It models three views common to
intraday liquidity operations:

| View | Contents |
|---|---|
| Nostro Transfer | transfer instructions, approval workflow, message status |
| Client | account balances, calculated-vs-EOD reconciliation, FX-translated display currency |
| Business Ledger Txn | ledger postings, cashflow type, counterparty, trade status, netting |

It also ships a data dictionary, the view controls in effect, and an FX table.
That metadata does real work: the caveats the assistant reports are drawn from
it — derived-column formulas, enum domains, the filters already applied to each
view — rather than invented.

## Setup

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in **one** of:

- **OpenAI** — `OPENAI_API_KEY` (starts with `sk-`), optionally `OPENAI_MODEL`.
- **Azure OpenAI** — `OPENAI_API_KEY` (32-char hex), plus `AZURE_OPENAI_ENDPOINT`
  and `AZURE_OPENAI_DEPLOYMENT`. Corporate-issued keys are usually this kind.

Then:

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8099
```

Open http://localhost:8099. The status badge shows the provider and model in use,
or tells you exactly what is missing.

## Access

The application opens on a password prompt and nothing behind it is reachable
until you sign in. The gate is enforced on the server, not just in the browser:
every `/api` route except `/api/ping`, `/api/session` and `/api/login` refuses a
request without a valid session cookie, so hiding the interface is not the only
thing standing between a stranger and the API key this tool spends.

The default password is `Studio2026`, set in `app/auth.py`. **That default is in
a public repository and is therefore public knowledge.** Any instance you
actually want closed must set its own:

- `APP_PASSWORD` — the password that unlocks the application.
- `APP_SECRET` — signs the session cookie. When unset a new one is generated at
  startup, so restarting signs everyone out.

The Render blueprint asks for `APP_PASSWORD` once and generates `APP_SECRET`,
so neither is ever committed.

## Tests

| Command | Covers | Needs a key |
|---|---|---|
| `.venv\Scripts\python.exe tests_smoke.py` | data loading, queries, charts, exports | no |
| `.venv\Scripts\python.exe tests_api.py` | full API flow and guard rails, via a stub model | no |
| `.venv\Scripts\python.exe tests_live.py` | real end-to-end against the live model | yes |

The first two need no API key, so the deterministic core can be verified offline.

## Layout

```
app/
  data_access.py    loads the workbook; runs every query deterministically
  llm_client.py     provider interface (OpenAI / Azure OpenAI)
  prompts.py        system prompts, grounded in the dataset's own metadata
  orchestrator.py   the state machine and its validation gates
  report_builder.py matplotlib charts
  pdf_export.py     PDF assembly (reportlab)
  md_export.py      the Markdown companion
  main.py           FastAPI routes
static/             front end, no build step
data/               the synthetic workbook
exports/            generated charts and reports (git-ignored)
```

## Design

The visual system is documented in [DESIGN.md](DESIGN.md) — tokens, typography,
shape, and the two validated chart themes. Change values there and in
`static/styles.css` together.

## Design notes

**Validate before confirming.** The model's chosen query is dry-run against the
real data before you are asked to confirm, so an impossible view fails with a
clear message instead of after you have agreed to it. If the query is invalid,
the actual error is fed back to the model once so it can correct itself —
typically a wrong column name.

**Charts.** Every chart plots one measure, so it is single-series: one hue, no
legend, no value-ramp across categories. A scalar result renders as a headline
number rather than a one-bar chart. There is no pie option — a six-segment
categorical palette could not clear the colour-vision-deficiency separation gate,
and bars compare magnitudes more accurately anyway.

**Glyph columns.** The reconciliation column stores a tick or a cross. Language
models cannot reliably reproduce those characters, so plain words like
"unmatched" are accepted and mapped back to the stored glyph. An unrecognised
value raises an error rather than silently returning zero rows — which would read
as a genuine finding of "nothing failed".

**No Office automation.** PDF generation uses reportlab directly rather than
driving Word or Excel via COM, which disturbs open applications and hangs on
export under some managed desktop configurations.

## Scope

This is a proof of concept on synthetic data. Running it against real data would
additionally need: an approved enterprise AI route (typically Azure or Bedrock
rather than a direct API key), read access to the systems producing these views,
user entitlements covering who may see which entity, currency and desk, and audit
logging appropriate to intraday liquidity reporting obligations.
