# LedgerLink — Acceptance

Seeded-defect test (system python3, stdlib only): `test/acceptance_test.py`
Full output: `test/acceptance_results.txt`

**Result: 15/15 checks passed.**

Seeded CSV (8 rows) plants: an exact duplicate row, a row with an
unparseable date, a `(4.50)` bracket-negative amount, and two
ambiguous day/month dates (01/05/2026, 03/04/2026).

Verified:
- Conservation: 8 in = 6 converted + 1 duplicate + 1 rejected
- Duplicate removed and counted; broken row rejected with the run
  still completing; ambiguous rows flagged [3, 7], read month-first
- Net total 2295.71 exact in the report AND on independent re-parse
  of the written QIF and OFX files
- FITIDs unique in OFX; QBO contains INTU.BID
- Bracket negative written as T-4.50 in QIF
- Split Debit/Credit CSV converts with net 925.50; sign-flip run
  gives exactly −925.50 (credit-card statement behaviour)
- Determinism: two runs produce byte-identical QIF
- GUI compiles (`py_compile` clean); engine is stdlib-only
