# Reference data model

What a bank data lake would let us attach to this transaction data, and what each hop makes answerable.

Generated from `reference_model.py` by `generate_reference.py`, so this document always describes the tables that actually exist. The data is synthetic: **names are invented, countries, regions, ISO codes and currencies are real**, because a jurisdiction question answered with invented geography tells you nothing.

> The sanctions, FATF and risk-tier columns are illustrative structure so the shape of such a question can be demonstrated. **They are not a compliance source and must never be used for screening.**

## Why two rounds

Round 1 attaches to a column that exists in the live views. Round 2 attaches to something round 1 produced, so it is only reachable in two hops - and that is where the interesting questions live. "Intraday usage for Russian counterparties" is not answerable from the transaction data or from round 1 alone; it needs counterparty → country of incorporation → sanctions regime.

## Round 1

Keyed directly off a column in the live data.

### `account_master` — Account

**Joins from:** `client.Account`, `business_ledger.Account`, `nostro_transfer.Source Account`, `nostro_transfer.Target Account`
  
**Grain:** one row per account number  
**Rows generated:** 920

| Attribute | Notes |
|---|---|
| Account Type | what the account is for |
| Account Purpose |  |
| Account Status |  |
| Opened Date |  |
| Last Review Date | when the account was last attested |
| Owning Legal Entity | → `legal_entity_master` |
| Booking Branch | → `branch_master` |
| Product Code | → `product_master` |
| IBAN |  |
| BIC |  |
| Overdraft Permitted |  |
| Intraday Limit (GBP) | the limit this account may run |

### `counterparty_master` — Counterparty

**Joins from:** `business_ledger.Counterparty`
  
**Grain:** one row per counterparty  
**Rows generated:** 77

| Attribute | Notes |
|---|---|
| Counterparty Legal Name |  |
| LEI | legal entity identifier |
| Country of Incorporation | the jurisdiction hop → `country_master` |
| Counterparty Type |  |
| Industry Sector | → `industry_master` |
| Credit Rating |  |
| Ultimate Parent | → `group_master` |
| Relationship Tier |  |
| Onboarded Date |  |
| KYC Refresh Due |  |
| Exposure Limit (GBP) |  |

### `venue_master` — Counterparty

**Joins from:** `nostro_transfer.Source Account Venue Location`, `nostro_transfer.Target Account Venue Location`
  
**Grain:** one row per correspondent venue  
**Rows generated:** 117

| Attribute | Notes |
|---|---|
| Venue BIC |  |
| Venue Country | → `country_master` |
| Venue City | taken from the venue's own name |
| Venue Type |  |
| Cut-off Time (local) |  |
| Operating Timezone |  |
| SLA Tier |  |
| Nostro Agent |  |

### `legal_entity_master` — Enterprise Reference Data

**Joins from:** `client.Legal Entity`
  
**Grain:** one row per group legal entity  
**Rows generated:** 14

| Attribute | Notes |
|---|---|
| Entity LEI |  |
| Country of Domicile | → `country_master` |
| Entity Type |  |
| Lead Regulator | → `regulator_master` |
| Consolidation Group |  |
| Reporting Currency |  |

### `currency_master` — Enterprise Reference Data

**Joins from:** `nostro_transfer.Currency`, `client.Currency`, `business_ledger.CCY (Local)`
  
**Grain:** one row per ISO currency  
**Rows generated:** 27

| Attribute | Notes |
|---|---|
| Currency Name |  |
| Issuing Country | → `country_master` |
| Convertibility |  |
| Settlement Lag (days) |  |
| CLS Eligible |  |
| Restricted Currency |  |

### `payment_scheme_master` — Payment Scheme

**Joins from:** `nostro_transfer.Sending Strategy`
  
**Grain:** one row per sending mechanism  
**Rows generated:** 3

| Attribute | Notes |
|---|---|
| Scheme Name |  |
| Network Type |  |
| Settlement Model | RTGS, deferred net, or on-us |
| Scheme Operator | → `scheme_operator_master` |
| Cut-off Time (UTC) |  |
| Operating Days |  |
| Message Standard |  |

### `desk_master` — Book

**Joins from:** `business_ledger.Sub Branch`
  
**Grain:** one row per booking desk  
**Rows generated:** 8

| Attribute | Notes |
|---|---|
| Desk Name |  |
| Business Line | → `business_line_master` |
| Desk Location |  |
| Desk Country | → `country_master` |
| Desk Timezone |  |
| Desk Head |  |
| Cost Centre |  |

### `book_master` — Book

**Joins from:** `business_ledger.Book Id`
  
**Grain:** one row per booking book  
**Rows generated:** 32

| Attribute | Notes |
|---|---|
| Book Name |  |
| Book Type |  |
| Business Line | → `business_line_master` |
| Owning Desk | derived from the book code |
| Cost Centre |  |

### `cashflow_type_master` — Settlement Instruction Data

**Joins from:** `business_ledger.Cashflow Type`
  
**Grain:** one row per cashflow classification  
**Rows generated:** 5

| Attribute | Notes |
|---|---|
| Cashflow Description |  |
| Cashflow Category |  |
| Settlement Method |  |
| Default Priority |  |
| Reconcilable |  |

### `gl_account_master` — Account

**Joins from:** `business_ledger.Ledger Account`
  
**Grain:** one row per general ledger account  
**Rows generated:** 1,169

| Attribute | Notes |
|---|---|
| GL Description |  |
| GL Class |  |
| Reporting Line |  |
| Reconciliation Owner |  |

### `user_master` — Enterprise Reference Data

**Joins from:** `nostro_transfer.Created By`, `nostro_transfer.Approved By`
  
**Grain:** one row per operator  
**Rows generated:** 75

| Attribute | Notes |
|---|---|
| User Id |  |
| Desk | → `desk_master` |
| Role |  |
| Approval Limit (GBP) |  |
| Location |  |
| Joined Date |  |

## Round 2

Keyed off an attribute produced by round 1.

### `country_master` — Enterprise Reference Data

**Reached via:** `counterparty_master.Country of Incorporation`, `venue_master.Venue Country`, `legal_entity_master.Country of Domicile`, `currency_master.Issuing Country`, `desk_master.Desk Country`
  
**Grain:** one row per country  
**Rows generated:** 43

| Attribute | Notes |
|---|---|
| ISO Country Code |  |
| Region |  |
| Sub Region |  |
| Sanctions Regime | ILLUSTRATIVE ONLY - never use for screening |
| FATF Listing | illustrative |
| EU or EEA Member |  |
| Jurisdiction Risk Tier | illustrative |
| Local Currency |  |

### `group_master` — Counterparty

**Reached via:** `counterparty_master.Ultimate Parent`
  
**Grain:** one row per counterparty group  
**Rows generated:** 24

| Attribute | Notes |
|---|---|
| Group Legal Name |  |
| Group Country | → `country_master` |
| Group Credit Rating |  |
| Group Exposure Limit (GBP) |  |
| Globally Systemic |  |

### `industry_master` — Counterparty

**Reached via:** `counterparty_master.Industry Sector`
  
**Grain:** one row per industry sector  
**Rows generated:** 7

| Attribute | Notes |
|---|---|
| Sector Description |  |
| NACE Code |  |
| Systemically Important |  |

### `regulator_master` — Enterprise Reference Data

**Reached via:** `legal_entity_master.Lead Regulator`
  
**Grain:** one row per regulator  
**Rows generated:** 6

| Attribute | Notes |
|---|---|
| Regulator Country | → `country_master` |
| Supervisory Regime |  |
| Intraday Reporting Required | whether BCBS 248 style reporting applies |
| Reporting Frequency |  |

### `business_line_master` — Book

**Reached via:** `desk_master.Business Line`, `book_master.Business Line`
  
**Grain:** one row per business line  
**Rows generated:** 5

| Attribute | Notes |
|---|---|
| Division |  |
| Business Line Head |  |
| P&L Owner |  |

### `product_master` — Account

**Reached via:** `account_master.Product Code`
  
**Grain:** one row per product  
**Rows generated:** 12

| Attribute | Notes |
|---|---|
| Product Name |  |
| Product Family |  |
| Revenue Line |  |

### `branch_master` — Account

**Reached via:** `account_master.Booking Branch`
  
**Grain:** one row per booking branch  
**Rows generated:** 12

| Attribute | Notes |
|---|---|
| Branch Name |  |
| Branch Country | → `country_master` |
| Branch City |  |
| Clearing System |  |

### `scheme_operator_master` — Payment Scheme

**Reached via:** `payment_scheme_master.Scheme Operator`
  
**Grain:** one row per scheme operator  
**Rows generated:** 4

| Attribute | Notes |
|---|---|
| Operator Country | → `country_master` |
| Oversight Authority |  |
| Operating Hours (UTC) |  |

## What this makes answerable

| Question | Path |
|---|---|
| Intraday usage for Russian counterparties | ledger → `counterparty_master` → `country_master`.Sanctions Regime |
| Exposure by jurisdiction risk tier | ledger → `counterparty_master` → `country_master`.Jurisdiction Risk Tier |
| Flow through restricted currencies | ledger → `currency_master`.Restricted Currency |
| Usage by regulated entity and its regulator | client → `legal_entity_master` → `regulator_master` |
| Which desks carry the systemic counterparties | ledger → `counterparty_master` → `group_master`.Globally Systemic |
| Settlement concentration by scheme operator | transfers → `payment_scheme_master` → `scheme_operator_master` |
| Dormant accounts still holding balance | client → `account_master`.Account Status |
| Value at venues with a late cut-off | transfers → `venue_master`.Cut-off Time |

## What is still not answerable

Reference data adds attributes to things that already appear. It cannot create records that were never captured:

- **why a payment failed** — no reason is recorded on the transaction
- **the dormant account estate** — accounts with no activity are absent from the extract entirely, so no lookup can size them
- **legal entity on a ledger posting** — the ledger carries no entity key, so `legal_entity_master` cannot be reached from it
- **the transfer that produced a posting**, for the 55% of ledger rows with no upstream reference
