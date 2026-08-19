# Executive view review — first ten views

Each view was defined, prompted through the tool, and the output reviewed
against what a liquidity manager would actually need. The raw transcript is
`exports/view_review.md`; this document is the judgement on it.

**All ten produced a report. That is the problem.** Running is not the same as
being useful, and in two cases the output was confidently wrong in a way a
reader could not detect.

> ## Status after Priorities 1 and 2
>
> | View | Before | Now |
> |---|---|---|
> | 1 Intraday peak | 2,350 rows, wrong metric, currencies added together | **20 rows, peak/usage/timing per day and currency** |
> | 2 Reconciliation | 481 rows | 481 rows — *Priority 3* |
> | 3 Failures | **3 rows (the AND of an OR)** | **199 rows** |
> | 4 Currency concentration | shares off a mixed-unit total | **shares on GBP equivalent** |
> | 5 Counterparty | correct | correct |
> | 6 Intraday timing | 477 rows across a month | **21 rows, one day** |
> | 7 Approval queue | 40 rows by creator | **2 age bands, £27bn over 24h** |
> | 8 Entity and desk | 899 rows | 1,150 rows — *Priority 3* |
> | 9 Day-over-day | local currency summed | **FX-translated automatically** |
> | 10 Largest movements | correct | correct |
>
> ## After Priority 3
>
> Result size is capped at 25 groups with the tail folded into "Other" (never on
> a time series, where the tail is "later" not "smaller"). Every report leads
> with two or three headline figures computed from the result. Time series carry
> a period average and mark days beyond two standard deviations. Concentration is
> reported on gross flow, so offsetting debits and credits cannot disguise it.
>
> **Seven of the ten are now saved as named views** users pick from the list —
> see `seed_views.py`. Three are deliberately not:
>
> | Not saved | Why |
> |---|---|
> | Reconciliation exceptions | Returns every break in the period as a flat list. Needs materiality, ageing and a repeat-offender count before it is a management view. |
> | Payment failures | The count is now right (199), but the breakdown fragments across currency and both venues into rows of one. Needs a coarser cut and a failure reason, which the data does not carry. |
> | Entity and desk performance | Groups by ledger account rather than desk, and legal entity cannot be attributed to the ledger view at all. Partly a data gap. |
>
> Re-running view 4 showed why the currency work mattered beyond tidiness: USD
> concentration was reported as 34.8% and is actually **49.7%**, while MXN read
> as the third-largest exposure on 202bn pesos and is in fact **0.74%** of the
> book. The old figure would have pointed a concentration discussion at the
> wrong currency.

---

## The two findings that matter most

### A. "Failed or rejected" silently became "failed AND rejected"

View 3 asked how many transfers failed **or** were rejected. Every filter is
combined with AND, so the tool answered the intersection:

| | Count |
|---|---|
| Failed | 98 |
| Rejected | 104 |
| **Reported (the AND)** | **3** |
| **Correct answer (the OR)** | **199** |

A 66× understatement, presented with a narrative recommending the team examine
"three transfers". An executive reading this concludes payment failure is
negligible. There is no OR in the filter engine, and nothing detected that the
question had asked for one.

### B. Invalid arithmetic is annotated, then performed anyway

View 1 summed local-currency amounts across currencies — adding THB to USD to
EUR — and reported a running total. The limitations said, correctly:

> The amounts are in different currencies and are therefore not directly
> comparable or summable.

And then the commentary said:

> The largest intraday liquidity usage occurred in CHF at 10:00 … bringing the
> cumulative value to 10,271,891,901.87. The operations team should closely
> examine …

The caveat layer identified that the report was meaningless and did not stop it.
A caveat is not a substitute for refusing to do invalid arithmetic, particularly
when the narrative then treats the number as real and recommends action on it.
The dataset *has* a display-currency column; it simply was not used.

---

## View-by-view

### 1. Intraday peak liquidity usage
*The largest intraday position we ran, by currency — the core BCBS 248 metric.*

**Produced:** 2,350 rows, hour × currency across the whole month, with a running
total that mixes currencies. Chart unreadable at 2,350 points.

**Missing:** Everything that makes it the metric. Peak usage is the **maximum of
the intraday cumulative net position, within a single day, per currency** — then
the worst such day in the period. What came back was a month-long running total
in mixed units.

**Should be:** One row per currency: peak usage, the day and time it peaked,
and the closing position. Plus a profile chart for the single worst day. Ten to
fifteen rows, not 2,350.

**Needs:** intraday cumulative reset per day; max-of-cumulative as a measure;
forced display currency; a "worst day" selector.

---

### 2. Reconciliation exceptions
*Which balances broke, how big, and what needs action today.*

**Produced:** 481 rows listing every break in the month. Correct, and unusable —
it is a data extract, not a view.

**Missing:** Materiality (which breaks matter), ageing (how long has this account
been breaking), and repetition (is it the same accounts every day).

**Should be:** A headline — total break value, count, worst account — then the
top 20 by size, with a column showing how many days that account has broken in
the period.

**Needs:** materiality threshold filtering; a count-of-days-in-breach calculation
per account; a summary tier above the detail.

---

### 3. Payment failures and rejections
*What failed, what value was at risk, is it concentrated.*

**Produced:** 3 rows. See finding A — the AND/OR error.

**Missing:** The other 196 items. Also no failure *reason*, and no split of
failed versus rejected, which are different operational problems.

**Should be:** Total failed value and count, split by failure type, then top
venues and currencies affected, and the largest individual failures.

**Needs:** OR filters; a failure-reason field in the data (does not exist).

---

### 4. Currency concentration
*Where liquidity sits, and whether any currency is an outsized share.*

**Produced:** 27 rows with share-of-total. Good shape — but computed on
local-currency amounts, so the shares are arithmetically meaningless.

**Missing:** Correct units. A concentration measure (top-3 share, or a
Herfindahl index) rather than leaving the reader to add up percentages.

**Should be:** Top 10 currencies by GBP-equivalent value with share, a "top 3
account for X%" headline, and everything below the tail folded into Other.

**Needs:** forced display currency for cross-currency aggregation; a
concentration statistic.

---

### 5. Counterparty concentration
*Which counterparties carry the flow.*

**Produced:** Top 10 with share of total, in display currency. **The best of the
ten** — right shape, right units, right size.

**Missing:** Direction (are they paying us or are we paying them), and a
comparison against the prior period.

**Should be:** As produced, plus net direction per counterparty and a
period-on-period move.

**Needs:** signed split by debit/credit; prior-period comparison.

---

### 6. Intraday flow timing
*When money arrives versus leaves.*

**Produced:** 477 rows — every hour of the month — pivoted into CR and DR
columns. The pivot is right; the period is wrong.

**Missing:** A single day, or an average day. 477 points on a line is not a
profile. Also no net line, which is the thing that actually matters.

**Should be:** 24 rows for one chosen day, showing credits, debits and net by
hour; or an averaged profile across the month with the peak hour marked.

**Needs:** default scoping to one day for intraday questions; an average-across-
days mode; a net measure alongside the two directions.

---

### 7. Approval pipeline
*What is waiting on a human, and for how long.*

**Produced:** 40 rows, count and value grouped by the person who created each
item.

**Missing:** **Ageing** — the entire point. "Who created it" is far less useful
than "how long has it been sitting". Nothing indicates urgency.

**Should be:** Count and value bucketed by age (under 1h, 1–4h, 4–24h, over
24h), with the oldest items listed individually.

**Needs:** an elapsed-time calculation between a timestamp and now (or the
period end); bucketing of that elapsed time.

---

### 8. Entity and desk performance
*How activity breaks down across entities and desks.*

**Produced:** 899 rows — sub-branch × individual account.

**Missing:** The right level. There are 8 desks; the answer should have 8 rows.
It also could not use Legal Entity, which lives on the Client view and is not
joinable to the ledger.

**Should be:** One row per desk with flow, transaction count and share; entity
level above that.

**Needs:** the model to resist grouping by a high-cardinality identifier when
asked for a management breakdown; entity attribution on the ledger view (a data
gap).

---

### 9. Day-over-day trend
*Is today normal.*

**Produced:** 22 rows, one per business day. Right shape and size.

**Missing:** Correct units again (local currency summed across currencies), and
any sense of what "normal" is — no average, no variance band, no flagging of the
outlier days.

**Should be:** Daily total in GBP equivalent, with the period average as a
reference line and unusual days marked.

**Needs:** forced display currency; a reference/average line on time series;
outlier flagging.

---

### 10. Largest individual movements
*Which single transactions warrant attention.*

**Produced:** Top 20 with reference, account, counterparty, currency and both
amounts. Correct and useful.

**Missing:** Only context — how unusual is each of these relative to normal for
that account or counterparty.

**Should be:** As produced, plus a "times the account's median" column.

**Needs:** per-group comparison against a baseline statistic.

---

## What to build, in priority order

Grouped by how many views each unblocks.

### Priority 1 — correctness. These produce wrong answers today.

1. **OR / filter groups.** `{"any": [...]}` alongside the current implicit AND,
   and the model instructed that "X or Y" means a union. *Fixes view 3, and the
   whole class of "or" questions.*
2. **Block invalid cross-currency arithmetic.** When aggregating a local-currency
   amount across more than one currency, either switch to the display-currency
   column automatically or refuse. A caveat is not enough — it did not stop a
   nonsense figure being recommended for action. *Fixes views 1, 3, 4, 9.*

### Priority 2 — the metrics that make it an MI tool

3. **Intraday cumulative that resets daily, and max-of-cumulative.** Unlocks
   peak intraday usage, the metric the whole programme is about. *View 1.*
4. **Elapsed-time / ageing calculations and buckets.** *Views 2 and 7.*
5. **Default period scoping.** An intraday question means one day unless the
   user says otherwise. *Views 1 and 6.*

### Priority 3 — executive framing

6. **Result-size discipline.** A management view returning 481 or 2,350 rows is
   a failed view. Cap grouped output, fold the tail into "Other", and tell the
   model that a breakdown a person reads is 5–20 rows.
7. **Headline tier.** Every view should lead with two or three numbers (total,
   count, worst case) before the breakdown.
8. **Baselines and outlier marking.** Period average, variance band, and marking
   the days or values that sit outside it. *Views 9, 10, 2.*
9. **Concentration statistics.** Top-N share and a concentration index. *Views 4
   and 5.*

### Data gaps this surfaced (for the next extract)

- **Failure reason** on transfers — without it, "why did payments fail" is
  unanswerable, only "how many".
- **Legal entity on the ledger view** — entity-level MI is currently impossible
  because entity lives only on the Client view.
- **Direction/counterparty on transfers** — to net counterparty exposure rather
  than gross flow.

---

## How this review was run

`tests_views.py` holds the ten definitions and runs each end to end, writing the
transcript to `exports/view_review.md`. Re-run it after implementing any of the
above to see the same ten views change:

```bash
python tests_views.py
python tests_views.py --only 1
```

The next ten views should be defined the same way — written as the question a
manager would ask, run, then judged on whether the answer is one they could act
on.
