# Anonymisation

Every extract is anonymised through one persistent mapping — the **golden
source** — so the same real value becomes the same surrogate in every file,
forever.

**The golden source is never committed.** It lives at
`anonymisation/mapping.json`, which is git-ignored. It is the only place real
values are ever written. This document describes the method; it contains no
values.

## Why a persistent mapping

The obvious alternatives both fail:

- **Hashing** produces stable surrogates but unreadable ones, and a short
  hash over a small domain (a few hundred accounts) is trivially reversible
  by brute force.
- **Anonymising each extract independently** breaks continuity: account 12345
  becomes "Account A" in Monday's file and "Account C" in Tuesday's, so nothing
  can be analysed across extracts.

A stored mapping gives stable, readable surrogates and lets us prove what was
replaced.

## Commands

```bash
# See what a new extract contains that is not yet mapped. Changes nothing.
python anonymise.py scan <file.xlsx>

# Anonymise it, extending the golden source with any new values.
python anonymise.py apply <file.xlsx> --out <anonymised.xlsx>

# Confirm nothing escaped.
python anonymise.py audit <anonymised.xlsx>

# Review the mapping.
python anonymise.py report [--category counterparty]
```

**Always run `scan` first.** It exits non-zero if the file has text columns not
yet classified, which would otherwise pass through untouched.

Then `apply`, then `audit`. The audit re-reads the output and fails if any
mapped real value is still present.

## What gets replaced

Categories are assigned per column in `COLUMN_CATEGORIES` in `anonymise.py`.
A column that is not listed is **passed through unchanged**, which is why `scan`
refuses to stay quiet about unclassified text columns.

| Category | Covers | Surrogate keeps |
|---|---|---|
| `account_number` | account identifiers | digit count |
| `sort_code` | routing codes | digit count |
| `account_name` | human-readable account labels | — |
| `ledger_account` | structured GL codes | dot structure and numbering |
| `book_id` | trading/treasury books | length |
| `legal_entity` | legal entities | legal-form suffix style |
| `counterparty` | counterparties | — |
| `venue` | venue / location descriptions | — |
| `person` | operators and approvers | first/last name shape |
| `system` | internal system names | length |
| `system_status` | statuses embedding a system name | the outcome word |
| `branch_code` | branch / desk codes | length |
| `type_code`, `status_code` | classification codes | the generic words |
| `org_unit` | groups, teams, desks | — |
| `reference` | business references | letter/digit pattern |
| `free_text` | comments, narratives | replaced wholesale |

Shape is preserved deliberately: an account number that stops being 13 digits,
or a code that stops being 4 characters, can break downstream parsing and makes
the anonymised set behave differently from the real one.

## What is deliberately NOT replaced

Public standards and generic banking vocabulary stay legible — `SWIFT`, `CHAPS`,
`TARGET2`, ISO currency codes, and words like `NOSTRO`, `LEDGER`, `CASHFLOW`,
`PENDING`, `RECEIVED`, `DR`, `CR`.

Renaming these buys no privacy and destroys meaning: `LEDGER_RECEIVED` turning
into `SYWDWR_RECEIVED` helps nobody. The list is `PUBLIC_TERMS` in
`anonymise.py`. **Add to it only when a term is genuinely industry-standard.**
When in doubt, let it be replaced — over-anonymising is recoverable, the reverse
is not.

## The trap: metadata sheets

The workbook's Data Dictionary, View Controls and Reference Data sheets are not
data tables, but they carry every sensitive value anyway — worked examples in the
dictionary, the full enum domains in Reference Data, group and team names in the
controls.

This is where anonymisation most often fails in practice: the data table gets
cleaned and the dictionary is forgotten. Measured on this workbook, cleaning only
the three data sheets left **43 real values** exposed in the metadata.

Those sheets have no columns to key off, so they are handled by a **value sweep**:
every cell whose exact value already exists in the mapping is replaced. The sweep
only substitutes values established elsewhere, so it can never invent a surrogate
for something unseen — but it does mean the data sheets must be processed in the
same run, which `apply` does.

## Adding a new column

1. Add it to `COLUMN_CATEGORIES` with the right category.
2. If no existing category fits, add one and give it a rule in `_surrogate`.
3. Re-run `scan` to confirm it is picked up, then `apply` and `audit`.

## If the mapping is lost

Every previously anonymised extract becomes unlinkable to any future one, and
there is no way to recover the correspondence. **Back it up** somewhere that is
not the repository — it is the only copy, by design.
