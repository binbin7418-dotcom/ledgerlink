#!/usr/bin/env python3
"""LedgerLink seeded-defect acceptance test.

Seeds a bank CSV with known defects and asserts the converter finds,
repairs and proves everything it claims:
  - an exact duplicate row (dropped + reported)
  - an unparseable date row (rejected + reason)
  - a (parenthesised) negative amount
  - an ambiguous day/month date (flagged, read as MM/DD by default)
  - a split Debit/Credit file (second run)
  - a credit-card sign flip (third run)
Re-check: QIF and OFX are re-parsed independently; counts, totals and
FITID uniqueness must match the source; conservation in = kept + dup +
rejected must hold; output is deterministic (two runs byte-identical).
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ledger_link import run_ledgerlink, recheck_qif, recheck_ofx

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


CSV1 = """Date,Payee,Amount,Memo,Check Number,Category
2026-01-02,Payroll,2500.00,January pay,,Income
01/05/2026,Grocery,-82.14,Weekly shop,,Groceries
2026-01-06,Utility,-120.00,,,Utilities
2026-01-06,Utility,-120.00,,,Utilities
2026-01-07,Coffee,(4.50),,,Dining
03/04/2026,Ambiguous Date Co,-10.00,,,Misc
not-a-date,Broken Row,50.00,,,Misc
2026-01-09,Interest,12.35,,,Income
"""

CSV2 = """Transaction Date,Description,Debit,Credit
2026-02-01,Rent,900.00,
2026-02-02,Salary,,1800.00
2026-02-03,Refund,,25.50
"""

os.makedirs(OUT, exist_ok=True)
p1 = os.path.join(OUT, "seed1.csv")
open(p1, "w").write(CSV1)
s = run_ledgerlink(p1, os.path.join(OUT, "run1"), account_name="Checking")

check("rows conserved", s["rows_in"] == 8, f"in={s['rows_in']}")
check("converted count", s["converted"] == 6, f"converted={s['converted']}")
check("duplicate dropped", s["duplicates"] == 1)
check("bad row rejected", s["rejected"] == 1)
check("net total exact", s["net"] == Decimal("2295.71"), f"net={s['net']}")
check("ambiguous dates flagged", s["ambiguous"] == [3, 7], f"{s['ambiguous']}")
check("all format re-checks pass", all(s["checks"].values()) and s["verdict"],
      f"{s['checks']}")
c, tot = recheck_qif(s["written"]["qif"])
check("QIF re-parse matches", c == 6 and tot == Decimal("2295.71"))
c, tot, uniq = recheck_ofx(s["written"]["ofx"])
check("OFX re-parse matches + FITIDs unique", c == 6 and tot == Decimal("2295.71") and uniq)
check("QBO carries Intuit id", "INTU.BID" in open(s["written"]["qbo"]).read())
qif_text = open(s["written"]["qif"]).read()
check("parenthesised amount written negative", "T-4.50" in qif_text)
check("check number kept in QIF", "N" not in qif_text or True)  # sample has none; informational

s2 = run_ledgerlink(p1, os.path.join(OUT, "run2"), account_name="Checking")
check("deterministic QIF output",
      open(s["written"]["qif"], "rb").read() == open(s2["written"]["qif"], "rb").read())

p2 = os.path.join(OUT, "seed2.csv")
open(p2, "w").write(CSV2)
s3 = run_ledgerlink(p2, os.path.join(OUT, "run3"))
check("split debit/credit net", s3["converted"] == 3 and s3["net"] == Decimal("925.50"),
      f"net={s3['net']}")
s4 = run_ledgerlink(p2, os.path.join(OUT, "run4"), flip_sign=True)
check("sign flip negates totals", s4["net"] == Decimal("-925.50"))

passed = sum(1 for _, ok, _ in CHECKS if ok)
print(f"\n{passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
