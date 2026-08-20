"""Generate the small workbook the test suite runs against.

The suite needs something small and fixed: running every test against a month of
data is slow, and pagination and row-count assertions have to describe something
exact. That role used to be filled by the original single-day extract, which was
removed because it still held values the golden source marks for replacement.

Rather than trimming a copy of the month workbook - which loses whatever is rare,
and a fixture missing a currency quietly stops testing the branches that handle
it - this generates a short period through the same code path. The result is
internally consistent, carries the phenomena the tool reports on (a
reconciliation break, several currencies, failed and pending transfers), and
cannot reintroduce what the old extract leaked.

The name matters: generate_reference.py takes its keys from every
data/synthetic_liquidity*.xlsx it finds, so the fixture must match that pattern
or the reference tables will not join to it.

Run this, then regenerate the reference data so it covers both workbooks:

    .venv\\Scripts\\python.exe make_fixture.py
    .venv\\Scripts\\python.exe generate_reference.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT = Path("data") / "synthetic_liquidity_fixture.xlsx"

# Chosen so the fixture is small but still contains what the tests need: four
# business days gives enough account-days for a reconciliation break to occur,
# and twenty active pairs gives eleven currencies rather than four. The seed is
# fixed so the counts the assertions compare against do not move.
ARGS = ["--days", "4", "--accounts", "60", "--active", "20", "--seed", "20260820"]


def main() -> int:
    command = [sys.executable, "generate_synthetic.py", "--out", str(OUT), *ARGS]
    print(" ".join(command))
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
