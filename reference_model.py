"""The reference-data model: what a bank data lake would hang off this data.

Declared once, here. `generate_reference.py` builds the tables from it and
`DATA_MODEL.md` is written from it, so the documentation cannot drift from what
was actually generated.

Two rounds, as scoped:

  Round 1 attaches to a column that exists in the live transaction views.
  Round 2 attaches to something round 1 produced - so it is reachable only by
  two hops, and is where questions like "usage for Russian counterparties"
  actually live: counterparty -> country of incorporation -> sanctions regime.

Everything generated is synthetic. Names are invented. Countries, regions,
currencies and ISO codes are real, because a jurisdiction question is
meaningless with invented geography.

IMPORTANT: the sanctions and risk fields are illustrative structure, not a
compliance source. They exist so the shape of such a question can be
demonstrated, and must never be used for screening.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Round 1 - keyed directly off a column in the live views.
#   key:        (view, column) it joins to
#   grain:      what one row represents
#   attributes: [(column, kind, note)] where kind drives generation
# --------------------------------------------------------------------------
ROUND_1 = {
    "account_master": {
        "domain": "Account",
        "key": ("Account", "account"),
        "joins_from": [("client", "Account"), ("business_ledger", "Account"),
                       ("nostro_transfer", "Source Account"),
                       ("nostro_transfer", "Target Account")],
        "grain": "one row per account number",
        "attributes": [
            ("Account Type", "choice:Nostro,Vostro,Client Money,House,Suspense,Settlement",
             "what the account is for"),
            ("Account Purpose", "choice:Operating,Collateral,Fees,Liquidity Buffer,"
                                "Client Segregated,Settlement", ""),
            ("Account Status", "weighted:Active:0.93,Dormant:0.05,Blocked:0.02", ""),
            ("Opened Date", "date_past", ""),
            ("Last Review Date", "date_recent", "when the account was last attested"),
            ("Owning Legal Entity", "fk:legal_entity_master", ""),
            ("Booking Branch", "fk:branch_master", ""),
            ("Product Code", "fk:product_master", ""),
            ("IBAN", "iban", ""),
            ("BIC", "bic", ""),
            ("Overdraft Permitted", "weighted:No:0.8,Yes:0.2", ""),
            ("Intraday Limit (GBP)", "amount:1e6:5e9", "the limit this account may run"),
        ],
    },
    "counterparty_master": {
        "domain": "Counterparty",
        "key": ("Counterparty", "counterparty"),
        "joins_from": [("business_ledger", "Counterparty")],
        "grain": "one row per counterparty",
        "attributes": [
            ("Counterparty Legal Name", "legal_name", ""),
            ("LEI", "lei", "legal entity identifier"),
            ("Country of Incorporation", "fk:country_master", "the jurisdiction hop"),
            ("Counterparty Type", "choice:Bank,Broker Dealer,Central Bank,Corporate,"
                                  "Fund,Clearing House", ""),
            ("Industry Sector", "fk:industry_master", ""),
            ("Credit Rating", "weighted:AAA:0.04,AA:0.14,A:0.32,BBB:0.28,BB:0.14,B:0.08", ""),
            ("Ultimate Parent", "fk:group_master", ""),
            ("Relationship Tier", "weighted:Tier 1:0.15,Tier 2:0.3,Tier 3:0.55", ""),
            ("Onboarded Date", "date_past", ""),
            ("KYC Refresh Due", "date_future", ""),
            ("Exposure Limit (GBP)", "amount:5e7:2e10", ""),
        ],
    },
    "venue_master": {
        "domain": "Counterparty",
        "key": ("Venue Location", "venue"),
        "joins_from": [("nostro_transfer", "Source Account Venue Location"),
                       ("nostro_transfer", "Target Account Venue Location")],
        "grain": "one row per correspondent venue",
        "attributes": [
            ("Venue BIC", "bic", ""),
            ("Venue Country", "fk:country_master", ""),
            ("Venue City", "city_from_name", "taken from the venue's own name"),
            ("Venue Type", "choice:Correspondent Bank,Central Bank,Clearing House,"
                           "Custodian", ""),
            ("Cut-off Time (local)", "choice:14:00,15:00,15:30,16:00,16:30,17:00,18:00", ""),
            ("Operating Timezone", "timezone_from_city", ""),
            ("SLA Tier", "weighted:Gold:0.2,Silver:0.45,Bronze:0.35", ""),
            ("Nostro Agent", "weighted:Yes:0.6,No:0.4", ""),
        ],
    },
    "legal_entity_master": {
        "domain": "Enterprise Reference Data",
        "key": ("Legal Entity", "legal_entity"),
        "joins_from": [("client", "Legal Entity")],
        "grain": "one row per group legal entity",
        "attributes": [
            ("Entity LEI", "lei", ""),
            ("Country of Domicile", "fk:country_master", ""),
            ("Entity Type", "choice:Bank,Branch,Subsidiary,Representative Office", ""),
            ("Lead Regulator", "fk:regulator_master", ""),
            ("Consolidation Group", "choice:Group,Ring-fenced Bank,Non ring-fenced", ""),
            ("Reporting Currency", "ccy_of_country", ""),
        ],
    },
    "currency_master": {
        "domain": "Enterprise Reference Data",
        "key": ("Currency", "currency"),
        "joins_from": [("nostro_transfer", "Currency"), ("client", "Currency"),
                       ("business_ledger", "CCY (Local)")],
        "grain": "one row per ISO currency",
        "attributes": [
            ("Currency Name", "ccy_name", ""),
            ("Issuing Country", "fk:country_master", ""),
            ("Convertibility", "ccy_convertibility", ""),
            ("Settlement Lag (days)", "choice:0,1,2", ""),
            ("CLS Eligible", "ccy_cls", ""),
            ("Restricted Currency", "ccy_restricted", ""),
        ],
    },
    "payment_scheme_master": {
        "domain": "Payment Scheme",
        "key": ("Sending Strategy", "scheme"),
        "joins_from": [("nostro_transfer", "Sending Strategy")],
        "grain": "one row per sending mechanism",
        "attributes": [
            ("Scheme Name", "scheme_name", ""),
            ("Network Type", "scheme_network", ""),
            ("Settlement Model", "scheme_settlement", "RTGS, deferred net, or on-us"),
            ("Scheme Operator", "fk:scheme_operator_master", ""),
            ("Cut-off Time (UTC)", "choice:14:00,15:00,16:00,17:00", ""),
            ("Operating Days", "choice:Mon-Fri,Mon-Sun", ""),
            ("Message Standard", "scheme_standard", ""),
        ],
    },
    "desk_master": {
        "domain": "Book",
        "key": ("Sub Branch", "desk"),
        "joins_from": [("business_ledger", "Sub Branch")],
        "grain": "one row per booking desk",
        "attributes": [
            ("Desk Name", "desk_name", ""),
            ("Business Line", "fk:business_line_master", ""),
            ("Desk Location", "city", ""),
            ("Desk Country", "fk:country_master", ""),
            ("Desk Timezone", "timezone_from_city", ""),
            ("Desk Head", "person_name", ""),
            ("Cost Centre", "cost_centre", ""),
        ],
    },
    "book_master": {
        "domain": "Book",
        "key": ("Book Id", "book"),
        "joins_from": [("business_ledger", "Book Id")],
        "grain": "one row per booking book",
        "attributes": [
            ("Book Name", "book_name", ""),
            ("Book Type", "weighted:Banking:0.7,Trading:0.3", ""),
            ("Business Line", "fk:business_line_master", ""),
            ("Owning Desk", "book_desk", "derived from the book code"),
            ("Cost Centre", "cost_centre", ""),
        ],
    },
    "cashflow_type_master": {
        "domain": "Settlement Instruction Data",
        "key": ("Cashflow Type", "cashflow_type"),
        "joins_from": [("business_ledger", "Cashflow Type")],
        "grain": "one row per cashflow classification",
        "attributes": [
            ("Cashflow Description", "cashflow_desc", ""),
            ("Cashflow Category", "choice:Securities,Treasury,Client,Fees,Interbank", ""),
            ("Settlement Method", "choice:DVP,FOP,Cash,Netted", ""),
            ("Default Priority", "weighted:Normal:0.7,High:0.2,Urgent:0.1", ""),
            ("Reconcilable", "weighted:Yes:0.85,No:0.15", ""),
        ],
    },
    "gl_account_master": {
        "domain": "Account",
        "key": ("Ledger Account", "gl_account"),
        "joins_from": [("business_ledger", "Ledger Account")],
        "grain": "one row per general ledger account",
        "attributes": [
            ("GL Description", "gl_desc", ""),
            ("GL Class", "choice:Asset,Liability,Suspense,Contra", ""),
            ("Reporting Line", "choice:Cash and balances,Loans and advances,"
                               "Other assets,Deposits", ""),
            ("Reconciliation Owner", "person_name", ""),
        ],
    },
    "user_master": {
        "domain": "Enterprise Reference Data",
        "key": ("User", "user"),
        "joins_from": [("nostro_transfer", "Created By"),
                       ("nostro_transfer", "Approved By")],
        "grain": "one row per operator",
        "attributes": [
            ("User Id", "user_id", ""),
            ("Desk", "fk:desk_master", ""),
            ("Role", "weighted:Operator:0.55,Senior Operator:0.25,Approver:0.15,"
                     "Manager:0.05", ""),
            ("Approval Limit (GBP)", "amount:1e6:1e10", ""),
            ("Location", "city", ""),
            ("Joined Date", "date_past", ""),
        ],
    },
}

# --------------------------------------------------------------------------
# Round 2 - keyed off something round 1 produced, so reachable in two hops.
# --------------------------------------------------------------------------
ROUND_2 = {
    "country_master": {
        "domain": "Enterprise Reference Data",
        "key": ("Country", "country"),
        "reached_via": ["counterparty_master.Country of Incorporation",
                        "venue_master.Venue Country",
                        "legal_entity_master.Country of Domicile",
                        "currency_master.Issuing Country",
                        "desk_master.Desk Country"],
        "grain": "one row per country",
        "attributes": [
            ("ISO Country Code", "country_iso", ""),
            ("Region", "country_region", ""),
            ("Sub Region", "country_subregion", ""),
            ("Sanctions Regime", "country_sanctions",
             "ILLUSTRATIVE ONLY - never use for screening"),
            ("FATF Listing", "country_fatf", "illustrative"),
            ("EU or EEA Member", "country_eu", ""),
            ("Jurisdiction Risk Tier", "country_risk", "illustrative"),
            ("Local Currency", "country_ccy", ""),
        ],
    },
    "group_master": {
        "domain": "Counterparty",
        "key": ("Ultimate Parent", "group"),
        "reached_via": ["counterparty_master.Ultimate Parent"],
        "grain": "one row per counterparty group",
        "attributes": [
            ("Group Legal Name", "legal_name", ""),
            ("Group Country", "fk:country_master", ""),
            ("Group Credit Rating", "weighted:AAA:0.05,AA:0.2,A:0.35,BBB:0.25,BB:0.15", ""),
            ("Group Exposure Limit (GBP)", "amount:1e8:5e10", ""),
            ("Globally Systemic", "weighted:No:0.85,Yes:0.15", ""),
        ],
    },
    "industry_master": {
        "domain": "Counterparty",
        "key": ("Industry Sector", "industry"),
        "reached_via": ["counterparty_master.Industry Sector"],
        "grain": "one row per industry sector",
        "attributes": [
            ("Sector Description", "sector_desc", ""),
            ("NACE Code", "nace", ""),
            ("Systemically Important", "weighted:No:0.7,Yes:0.3", ""),
        ],
    },
    "regulator_master": {
        "domain": "Enterprise Reference Data",
        "key": ("Lead Regulator", "regulator"),
        "reached_via": ["legal_entity_master.Lead Regulator"],
        "grain": "one row per regulator",
        "attributes": [
            ("Regulator Country", "fk:country_master", ""),
            ("Supervisory Regime", "choice:Basel III,Basel III (local),Equivalent", ""),
            ("Intraday Reporting Required", "weighted:Yes:0.7,No:0.3",
             "whether BCBS 248 style reporting applies"),
            ("Reporting Frequency", "choice:Daily,Monthly,Quarterly", ""),
        ],
    },
    "business_line_master": {
        "domain": "Book",
        "key": ("Business Line", "business_line"),
        "reached_via": ["desk_master.Business Line", "book_master.Business Line"],
        "grain": "one row per business line",
        "attributes": [
            ("Division", "choice:Corporate and Investment Bank,Commercial Bank,"
                         "Treasury,Private Bank", ""),
            ("Business Line Head", "person_name", ""),
            ("P&L Owner", "person_name", ""),
        ],
    },
    "product_master": {
        "domain": "Account",
        "key": ("Product Code", "product"),
        "reached_via": ["account_master.Product Code"],
        "grain": "one row per product",
        "attributes": [
            ("Product Name", "product_name", ""),
            ("Product Family", "choice:Cash Management,Custody,Trade Finance,"
                               "Securities Services,Lending", ""),
            ("Revenue Line", "choice:Fee,Net Interest Income,Trading", ""),
        ],
    },
    "branch_master": {
        "domain": "Account",
        "key": ("Booking Branch", "branch"),
        "reached_via": ["account_master.Booking Branch"],
        "grain": "one row per booking branch",
        "attributes": [
            ("Branch Name", "branch_name", ""),
            ("Branch Country", "fk:country_master", ""),
            ("Branch City", "city", ""),
            ("Clearing System", "clearing_system", ""),
        ],
    },
    "scheme_operator_master": {
        "domain": "Payment Scheme",
        "key": ("Scheme Operator", "scheme_operator"),
        "reached_via": ["payment_scheme_master.Scheme Operator"],
        "grain": "one row per scheme operator",
        "attributes": [
            ("Operator Country", "fk:country_master", ""),
            ("Oversight Authority", "operator_oversight", ""),
            ("Operating Hours (UTC)", "choice:07:00-18:00,00:00-24:00,08:00-17:00", ""),
        ],
    },
}

ALL_TABLES = {**ROUND_1, **ROUND_2}


def round_of(table: str) -> int:
    return 1 if table in ROUND_1 else 2
