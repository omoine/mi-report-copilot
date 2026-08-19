"""Turn the OCR candidate extract into a distribution profile.

The OCR output cannot supply real values - account numbers and references are
destroyed (0 of 450 rows produce a plausible identifier). What it can supply is
*shape*: which currencies actually occur and in what proportion, how statuses are
distributed, the magnitude of amounts, and how many venues and operators exist.

That shape is what makes generated data realistic, so this script extracts it and
writes a profile. Any legible real name is routed into the anonymisation golden
source and never written to the profile.

Usage:
    python profile_ocr.py <ocr_candidates.csv> [--out anonymisation/ocr_profile.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

import anonymise

ENGINES = ["g11", "b11", "g6"]

ISO_CURRENCIES = {
    "GBP", "EUR", "USD", "JPY", "CHF", "HKD", "SGD", "AUD", "NOK", "SEK", "ZAR",
    "CNY", "INR", "CAD", "NZD", "DKK", "PLN", "MXN", "TRY", "AED", "SAR", "THB",
    "KRW", "TWD", "BRL", "CZK", "HUF", "ILS", "RON", "RUB", "IDR", "MYR", "PHP",
}
TRANSFER_STATUSES = ["LEDGER_OR_CASHFLOW_RECEIVED", "LEDGER_RECEIVED",
                     "CASHFLOW_RECEIVED", "PENDING_APPROVAL", "FAILED"]
SENDING_STRATEGIES = ["FISS", "SWIFT", "INTERNAL"]
MESSAGE_STATUSES = ["FISS_ACKNOWLEDGED", "FISS_PENDING", "FISS_REJECTED"]

MIN_SCORE = 0.55


def _candidates(row: pd.Series, field: str) -> list[tuple[str, float]]:
    out = []
    for engine in ENGINES:
        value = str(row.get(f"{field}_{engine}", "") or "").strip()
        try:
            conf = float(row.get(f"{field}_{engine}_conf", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if value:
            out.append((value, conf))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _best_enum(cands: list[tuple[str, float]], domain: list[str]) -> str | None:
    """Snap a garbled reading onto the nearest allowed value.

    OCR confuses whole character classes (O/0, D/O, RECEIVED/RICENED), so exact
    matching recovers almost nothing. Scoring character overlap against a known
    domain, weighted by the engine's confidence, recovers most of it.
    """
    best, best_score = None, 0.0
    for value, conf in cands:
        n = _norm(value)
        if not n:
            continue
        for allowed in domain:
            a = _norm(allowed)
            if not a:
                continue
            if n == a:
                sim = 1.0
            else:
                common = sum((Counter(n) & Counter(a)).values())
                sim = common / max(len(a), len(n))
            score = sim * (0.6 + 0.4 * min(conf, 100) / 100)
            if score > best_score:
                best, best_score = allowed, score
    return best if best_score >= MIN_SCORE else None


def _amount_digits(row: pd.Series) -> int | None:
    """Digit count only. The decimal position disagrees between engines
    (202,000,000.000 against 202.000.000.000), so the magnitude itself cannot be
    trusted - but the number of digits is stable enough to size a distribution."""
    readings = []
    for value, conf in _candidates(row, "Value Amount"):
        digits = re.sub(r"\D", "", value)
        if digits:
            readings.append((digits, conf))
    if not readings:
        return None
    counts = Counter(d for d, _ in readings)
    top, n = counts.most_common(1)[0]
    if n < 2:
        top = max(readings, key=lambda x: x[1])[0]
    return len(top) if 4 <= len(top) <= 15 else None


def _distinct_names(df: pd.DataFrame, field: str, min_conf: float) -> list[str]:
    """Collect legible readings of a free-text name field.

    These are the only place real names survive OCR, so they are returned for
    routing into the golden source - never into the profile.
    """
    seen = Counter()
    for _, row in df.iterrows():
        for value, conf in _candidates(row, field):
            text = value.strip()
            if conf >= min_conf and len(text) > 3 and re.search(r"[A-Za-z]{3}", text):
                seen[text] += 1
    return [t for t, _ in seen.most_common()]


def build_profile(csv_path: Path) -> tuple[dict, dict[str, list[str]]]:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    n = len(df)

    currency = [_best_enum(_candidates(r, "Currency"), sorted(ISO_CURRENCIES))
                for _, r in df.iterrows()]
    status = [_best_enum(_candidates(r, "Transfer Status"),
                         [s.replace("_", " ") for s in TRANSFER_STATUSES])
              for _, r in df.iterrows()]
    strategy = [_best_enum(_candidates(r, "Sending Strategy"), SENDING_STRATEGIES)
                for _, r in df.iterrows()]
    message = [_best_enum(_candidates(r, "Message Status"),
                          [m.replace("_", " ") for m in MESSAGE_STATUSES])
               for _, r in df.iterrows()]
    digits = [d for d in (_amount_digits(r) for _, r in df.iterrows()) if d]

    def share(values: list[str | None]) -> dict[str, float]:
        found = [v for v in values if v]
        if not found:
            return {}
        counts = Counter(found)
        return {k.replace(" ", "_"): round(v / len(found), 4)
                for k, v in counts.most_common()}

    rows_per_image = df.groupby("image").size()

    profile = {
        "source_rows": n,
        "source_images": int(df["image"].nunique()),
        "rows_per_screen": int(rows_per_image.median()),
        "recovery_rate": {
            "currency": round(sum(v is not None for v in currency) / n, 3),
            "transfer_status": round(sum(v is not None for v in status) / n, 3),
            "sending_strategy": round(sum(v is not None for v in strategy) / n, 3),
            "message_status": round(sum(v is not None for v in message) / n, 3),
            "value_amount_digits": round(len(digits) / n, 3),
            "source_account": 0.0,
            "target_account": 0.0,
            "reference": 0.0,
        },
        "currency_mix": share(currency),
        "transfer_status_mix": share(status),
        "sending_strategy_mix": share(strategy),
        "message_status_mix": share(message),
        "amount_digit_mix": {str(k): round(v / len(digits), 4)
                             for k, v in Counter(digits).most_common()} if digits else {},
        "distinct_venue_readings": len(_distinct_names(df, "Source Account Venue Location", 55)),
        "distinct_operator_readings": len(_distinct_names(df, "Created By", 55)),
        "notes": [
            "Account numbers, target accounts and references were not recoverable: "
            "no row produced a plausible identifier under any engine.",
            "Amount magnitudes are unreliable because the engines disagree on the "
            "decimal separator; only the digit count is used.",
            "Venue and operator names were partially legible and are held in the "
            "anonymisation golden source, not here.",
        ],
    }

    names = {
        "venue": _distinct_names(df, "Source Account Venue Location", 55)
        + _distinct_names(df, "Target Account Venue Location", 55),
        "person": _distinct_names(df, "Created By", 55)
        + _distinct_names(df, "Approved By", 55),
    }
    return profile, names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path,
                        default=Path("anonymisation") / "ocr_profile.json")
    args = parser.parse_args()

    profile, names = build_profile(args.csv)

    # Register every legible real name so future extracts reuse the same
    # surrogate. The names themselves stay in the git-ignored golden source.
    mapping = anonymise._load()
    registered = 0
    for category, values in names.items():
        for value in values:
            anonymise._map_value(value, category, mapping)
            registered += 1
    anonymise._save(mapping)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    print(f"Profile written -> {args.out}")
    print(f"  {profile['source_rows']} rows from {profile['source_images']} screens "
          f"({profile['rows_per_screen']} rows per screen)")
    print("\nRecovery rate by field:")
    for field, rate in profile["recovery_rate"].items():
        bar = "#" * int(rate * 20)
        print(f"  {field:<22} {rate*100:5.1f}%  {bar}")
    print(f"\nCurrencies seen: {len(profile['currency_mix'])}")
    print(f"  top: {list(profile['currency_mix'].items())[:8]}")
    print(f"\n{registered} legible name readings routed into the golden source "
          f"(git-ignored); none written to the profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
