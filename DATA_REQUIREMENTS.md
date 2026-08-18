# What data would make this tool useful

Written after rebuilding the query engine and measuring it against 23 realistic
MI questions. The engine now answers 22 of them. The one it still cannot answer
well fails for a data reason, not a code reason — and that failure is
representative of the ceiling the current sample imposes.

**The ask in one line:** one complete business day of real rows, plus daily
totals for a month, plus the schema of a few concepts that are missing entirely.
Volume is not the constraint. Shape, span and structure are.

---

## The single most valuable thing

**One complete, unedited business day across all three views.**

Not a sample of rows — *every* row for one ordinary day. That one day gives:

- the true intraday shape (when volume actually arrives)
- realistic value distributions, including the long tail
- the real enum domains as they occur in production
- realistic cardinality of accounts, entities and counterparties
- the natural ratio of exceptions to clean records

From one real day plus monthly totals, a statistically faithful month can be
generated. From a thin slice of many days, nothing can.

If only one thing is obtainable, make it this.

---

## Why the current sample hits a ceiling

| View | Rows | Span |
|---|---|---|
| Nostro Transfer | 37 | ~1 day |
| Client | 24 | 1 snapshot |
| Business Ledger Txn | 60 | ~2 days |

**The failure this causes.** Asked for "the intraday profile of ledger
transactions", the tool correctly buckets by hour — and gets 34 buckets holding
about 1.8 rows each. It is a scatter of individual transactions, not a profile.
Intraday liquidity monitoring is fundamentally about *time shape*, and the sample
has no time shape to show. Roughly 300–500 ledger rows in one day would fix this
outright.

**Cardinality is also too low to rank or segment.** 24 client accounts across 4
currencies means "top 10 accounts by exposure" is nearly the whole population.
Ranking, concentration and outlier detection — the questions MI is actually for —
need enough population to have a meaningful tail.

---

## Priority 1 — dimensions that are too thin

These need no schema change, just more of what already exists.

| What | Now | Needed | Why |
|---|---|---|---|
| Ledger rows per day | ~30 | 300–500 | An hourly profile needs mass in each bucket |
| Business days | ~2 | 1 full day + 20 days of daily totals | Day-of-week and month-end effects |
| Client accounts | 24 | 150–300 | Ranking and concentration need a tail |
| Counterparties | ~8 | 40+ | Concentration analysis is the point |
| Currencies | 4 in client view | 8–12 | Cross-currency is where FX caveats matter |
| Legal entities | few | 5–10 | Entity-level MI is a standard cut |

## Priority 2 — concepts missing entirely

Each of these caused the tool to *correctly decline* a question a liquidity
manager would genuinely ask. Even a few rows showing the **schema** is enough —
column names, types, domains. I can synthesise volume once I know the shape.

| Concept | Fields needed | Unlocks |
|---|---|---|
| **Credit lines / limits** | account or counterparty, limit amount, currency, limit type | Utilisation against limit — the core BCBS 248 monitoring view. Currently unanswerable. |
| **Intraday balance timeline** | account, timestamp, running balance | Peak/trough intraday usage, largest negative position |
| **Payment queue / throttling** | payment ref, queued at, released at, queue reason, priority | Queue depth over time, delay analysis |
| **Thresholds and triggers** | trigger name, threshold value, breach timestamp, severity | Breach frequency, time-to-clear |
| **Settlement lifecycle** | received / validated / queued / settled timestamps | Where time is actually lost |
| **Payment priority** | priority code or urgency flag | Whether urgent payments are actually prioritised |

The first two are worth more than the rest combined.

## Priority 2b — one missing column that breaks cross-currency analysis

The Nostro Transfer View carries **no display-currency amount**. `Value Amount`
is always in the transfer's own currency, so any breakdown of that view across
currencies compares unlike units — one JPY against one GBP. Totals across
currencies from that view are not meaningful figures.

This shows up immediately: a distribution of transfer value by currency puts JPY
orders of magnitude above everything else, purely because of the unit. The tool
now flags this and switches to a logarithmic axis, but the honest fix is data.

**Add `Value Amount (Display)` and `Display CCY` to the nostro transfer extract**,
translated the same way the Client and Business Ledger views already are. Both of
those views have it; only this one does not. It is likely a one-line change to
whatever produces the extract, and it unlocks every cross-currency question on
transfer flow.

## Priority 3 — linkage between views

The three views currently share no reliable key, so the tool cannot answer
anything spanning them ("which transfers produced which ledger postings"). It
declines these, which is correct but limiting.

If a real join key exists in production — a transaction reference, an upstream
ID — including it in all three extracts unlocks a whole category of questions.
The `Upstream Transaction ID` column already exists in the ledger view but is
empty in the sample.

---

## What to bring, concretely

Best case, in priority order:

1. **One full business day**, all three views, every row. Anonymised is fine.
2. **Daily totals for ~20 business days** — a small table of date, view, row
   count, total value by currency. Enough to reproduce day-to-day variation.
3. **Schema stubs** for the Priority 2 concepts: a handful of rows each, or even
   just a column list with types and allowed values.
4. **A note on what "abnormal" looks like** — see below.

If access is tighter than that, the fallback that still helps is:

- **Distributions instead of rows**: transactions per hour, value percentiles per
  currency, counterparty concentration, share of records that are exceptions.
- **The data dictionary for the missing concepts**, with no data at all.

Anonymisation is not a problem. Account numbers, names and counterparties can all
be surrogate values, provided the *relationships* hold: the same account keeps
the same ID, and per-account volume and value stay proportionate.

---

## What "abnormal" looks like — the thing most easily forgotten

MI exists to surface exceptions. If synthetic data is statistically uniform,
every report is a flat, uninteresting line and the tool looks pointless in a
demo.

So the most valuable single sentence you can bring back is: **what does a bad day
look like, and how often does it happen?** For example:

- how often a reconciliation break occurs, and typical magnitude
- what share of payments fail or are rejected, and why
- what a liquidity spike looks like, and what causes it
- which counterparty or currency concentrations would worry a manager
- what threshold breach frequency is considered normal

With that, the generated data can carry realistic, *findable* anomalies — and the
tool can be demonstrated actually catching something, rather than drawing a
smooth curve.

---

## How the synthetic set gets built from this

1. Fit the intraday arrival curve, value distribution and category mixes from the
   real day.
2. Generate N business days by resampling that shape, adding day-of-week and
   month-end effects taken from the daily totals.
3. Scale up accounts, counterparties and currencies to realistic cardinality,
   preserving the concentration pattern (a few large, many small).
4. Inject anomalies at the stated frequencies — breaks, failures, spikes,
   breaches — so there is something genuine to find.
5. Add the Priority 2 concepts as new views, consistent with the existing keys.
6. Keep the workbook's Data Dictionary / View Controls structure, since the tool
   reads its caveats directly from those sheets.

Steps 1–4 are the ones that need real input. Steps 5–6 are mechanical.
