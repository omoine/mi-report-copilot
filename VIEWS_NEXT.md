# The next ten views

The first ten are built and saved. These are the ten to work through next, in
the same way: define the question a manager would ask, run it, judge the answer,
then either save it or record why not.

Each entry says what the view is for, what in the data supports it, and what it
will have to declare as unavailable. Several are worth building **because** of
what they cannot show — a view that visibly stops at the edge of the data is the
clearest possible statement of what is missing, and lands better with a data
owner than a list of requested fields.

---

## 11. Funding efficiency — how early do we fund?

**Question.** Of the money that leaves each day, how much was covered by money
that had already arrived? Are we funding ahead of outflows or chasing them?

**Supported by.** Ledger timestamps and the debit/credit mark, bucketed by hour,
with a running net position within the day.

**Cannot show.** Whether a payment was *deliberately* held. Without a queue or
release timestamp, late funding and a late instruction look identical.

---

## 12. Cut-off risk — what settles late in the day?

**Question.** How much value settles in the final hours, and with which venues?
A concentration late in the day is where an operational problem becomes a
liquidity problem.

**Supported by.** Transaction timestamps against a defined cut-off hour, split by
venue and currency.

**Cannot show.** The actual cut-off per currency and market. The tool will have
to take one as an input and say that it did.

---

## 13. Approval turnaround — how long does approval take?

**Question.** Median and 95th percentile time from creation to approval, by desk.
The queue view says what is waiting; this says how long waiting normally lasts.

**Supported by.** Created Time and Approved Time on the transfer view, with the
distribution mode already built.

**Cannot show.** Time spent waiting on the approver versus waiting on an upstream
dependency — only the total elapsed time exists.

---

## 14. Same-day reconciliation break movement

**Question.** Are breaks being cleared or accumulating? New breaks today, breaks
that persisted from yesterday, breaks that cleared.

**Supported by.** The daily balance snapshot across the period — an account's
match status can be compared day to day.

**Cannot show.** Why a break cleared: whether it was investigated and fixed, or
simply washed out by the next day's movement.

---

## 15. Currency pair flow — where does liquidity move between?

**Question.** Which currencies are we consistently short of and long in, and what
does that imply for the FX and funding plan?

**Supported by.** Net position by currency, and transfer source and target venue.

**Cannot show.** The actual FX trades that offset those positions. Nothing links
a transfer to an FX deal.

---

## 16. Venue reliability

**Question.** Which correspondent venues fail, reject or delay most, relative to
the volume we send them? A venue with three failures out of five matters more
than one with ten out of a thousand.

**Supported by.** Transfer status and message status by venue, as a rate rather
than a count.

**Cannot show.** Whether the fault was the venue's or ours, and the reason for
rejection.

---

## 17. Account dormancy and tail

**Question.** How much of the account estate is actually used? Of roughly six
thousand account/currency pairs, how many moved this month, and what does the
long tail cost us in reconciliation effort?

**Supported by.** Activity per account across the period against the full
population.

**Cannot show.** Why a dormant account is open — no purpose, owner or review date
is captured. This view exists largely to make that absence obvious.

---

## 18. Balance volatility by account

**Question.** Which accounts swing most day to day? Volatile balances are where
buffers are set and where forecasting is hardest.

**Supported by.** Daily swing per account across the period, using the
distribution and standard-deviation work already in place.

**Cannot show.** Whether volatility is expected for that account's purpose — a
settlement account and a fee account should behave differently, and nothing
distinguishes them.

---

## 19. Month-end and pattern effects

**Question.** How much do month-end, and the day of the week, change our funding
requirement? A number that should feed the buffer, not just be observed.

**Supported by.** Daily totals across the period with the baseline and outlier
marking already built.

**Cannot show.** Anything seasonal. One month cannot separate a month-end effect
from a one-off, and the view must say so rather than imply a pattern.

---

## 20. The data quality view

**Question.** How complete is the data these reports rest on? Blank counterparties,
missing approval timestamps, unmatched upstream references, dormant-account rows.

**Supported by.** Null and population counts across every column in all three
views — no new capability needed.

**Cannot show.** Whether a blank is wrong or simply not applicable.

**Why this one matters most.** Every other view inherits the quality of these
fields. A standing view of what is missing, sitting alongside the reports that
depend on it, is the most durable way to make the data gaps visible — and it is
the one view whose value comes entirely from what is absent.

---

## How to work through them

Same loop as the first ten:

```bash
python tests_views.py --only 11        # after adding it to VIEWS
python seed_views.py --replace          # once it earns a place
```

Judge each on whether a manager could act on the answer, not on whether the
query ran. Where the data stops, make the view say so — the gap is part of the
finding, and a mature view that declares its own limits is more useful than a
narrower one that hides them.

---

# Outcome

All ten were built and reviewed. **Nine are saved and available to users**; the
list now holds nineteen views.

## Capabilities added to get there

| Capability | Unlocked |
|---|---|
| Duration between two timestamps, in a stated unit | Approval turnaround |
| Time parts — hour of day, weekday, day of month | Cut-off risk, weekday pattern |
| Rates: share of rows in a group meeting a condition | Venue reliability |
| `quality` mode — completeness of every column | Data completeness |
| `nunique` — counting things rather than rows | Dormancy, and generally |
| Compact grouped distributions, sorted by spread | Balance volatility |

## Two defects this surfaced

**Subtracting two timestamps produced nanoseconds.** A 34-minute turnaround came
out as `2,040,000,000`, with no unit anywhere to say otherwise, and it silently
destroyed the two timestamp columns it was built from. Timestamp arithmetic is
now refused outright and points at the duration form, which always carries its
unit in the column name.

**Bucketing a date-only column by hour collapsed to a single row.** Every value
falls at midnight, so the result looked like an answer rather than a mistake.
That is now refused with an explanation.

## Not saved: account dormancy

It can only return a single number. The engine cannot compute a filtered and an
unfiltered aggregate side by side — "accounts with movement" and "accounts in
total" are two queries — so the comparison the question asks for cannot be made
in one view. Worth fixing if a comparison-to-baseline capability is ever built;
it would also serve period-on-period questions.

Separately, dormant accounts are absent from the extract altogether: roughly
6,000 account/currency pairs exist in the estate and only the ~900 that moved
appear. The view can only ever describe what is present, and says so.

## What the data completeness view immediately showed

`Upstream Transaction ID` is populated on **44.6%** of ledger rows. That is the
key linking ledger postings back to transfers, so slightly over half of all
postings cannot be traced to the transfer that caused them — which caps every
cross-view question that depends on that link.
