# LedgerLink

Convert bank CSV exports to QIF / OFX / QBO — with an independent re-read that proves counts, totals and transaction IDs match before you import.

## Run (dev)

```
python3 gui.py            # desktop app
python3 ledger_link.py transactions.csv out/   # CLI
python3 test/acceptance_test.py                # seeded acceptance test
```

## How it works

`ledger_link.py` sniffs the CSV dialect, maps headers by synonym
(date/amount/debit/credit/payee/memo/check/category), parses amounts
(bracket negatives, thousands separators, comma decimals) and dates
(multiple formats; ambiguous day/month rows are flagged, not guessed),
drops exact duplicates with a report entry, rejects unparseable rows
with reasons, and writes QIF + OFX 1.02 + QBO with deterministic
FITIDs. Every output is then re-parsed and compared (count, net
total, FITID uniqueness); the verdict lands in `conversion_report.md`.

Conservation is enforced: rows in = converted + duplicates + rejected.

## Scope

CSV in, QIF/OFX/QBO out. PDF statements are the sibling product
(StatementSift). OFX 2.x XML is not written.
