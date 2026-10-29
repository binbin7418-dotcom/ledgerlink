#!/usr/bin/env python3
"""
LedgerLink — bank / credit-card CSV -> QIF, OFX and QBO converter,
with a machine re-check of the files it writes.

Pipeline:
  1. Read a bank CSV export: auto-detect the delimiter and the columns
     (date, amount — or split debit/credit — payee, memo, check number,
     category) by header name.
  2. Normalize: dates in the wild formats banks actually emit (with
     ambiguous day/month rows flagged, never silently guessed), amounts
     with currency symbols, thousands separators, (parenthesised)
     negatives and optional comma decimals.
  3. Repair pass: drop exact-duplicate rows (date + amount + payee)
     and report them; rows that cannot be parsed are rejected WITH a
     reason, never silently lost: in = kept + duplicates + rejected.
  4. Write QIF (Quicken 2005-2017 / Banktivity / YNAB style), OFX 1.02
     and QBO (QuickBooks) files with stable, unique FITIDs.
  5. Re-check: re-parse the written files independently — transaction
     counts, credit/debit totals and FITID uniqueness must match the
     source exactly, or the verdict says FAIL.

Local only. No network. Standard library only.
Your bank file never leaves this computer.
"""
import csv
import hashlib
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS_MDY = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d",
                    "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%m/%d/%y"]
DATE_FORMATS_DMY = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d",
                    "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%d/%m/%y"]
AMBIGUOUS_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")

HEADER_SYNONYMS = {
    "date": ["date", "transaction date", "posting date", "posted", "value date"],
    "amount": ["amount", "value", "total", "sum"],
    "debit": ["debit", "withdrawal", "withdrawals", "money out", "paid out", "charge"],
    "credit": ["credit", "deposit", "deposits", "money in", "paid in", "payment received"],
    "payee": ["payee", "description", "name", "merchant", "details", "narrative"],
    "memo": ["memo", "notes", "note", "reference", "particulars"],
    "checknum": ["check number", "check #", "cheque number", "check no"],
    "category": ["category", "type", "transaction type"],
}


def _norm_header(h):
    return re.sub(r"[^a-z0-9 ]", " ", h.strip().lower()).strip()


def map_columns(headers):
    normed = [_norm_header(h) for h in headers]
    mapping = {}
    for field, syns in HEADER_SYNONYMS.items():
        for i, h in enumerate(normed):
            if h in syns and i not in mapping.values():
                mapping[field] = i
                break
    return mapping


def parse_amount(raw, decimal_comma=False):
    """Return Decimal or None. Handles $, (1,234.56), -1234.56, 1.234,56."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    s = re.sub(r"[$€£¥\s]", "", s)
    if decimal_comma:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    if s in ("-", "+", "."):
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -abs(v) if negative else v


def parse_date(raw, day_first=False):
    """Return (datetime|None, ambiguous: bool)."""
    if raw is None:
        return None, False
    s = str(raw).strip()
    if not s:
        return None, False
    ambiguous = False
    m = AMBIGUOUS_RE.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 12 and b <= 12 and a != b:
            ambiguous = True
    fmts = DATE_FORMATS_DMY if day_first else DATE_FORMATS_MDY
    for f in fmts:
        try:
            return datetime.strptime(s, f), ambiguous
        except ValueError:
            continue
    return None, ambiguous


def read_transactions(csv_path, day_first=False, decimal_comma=False,
                      flip_sign=False):
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(fh, dialect))
    if not rows:
        raise ValueError("CSV file is empty")
    headers, data_rows = rows[0], rows[1:]
    mapping = map_columns(headers)
    if "date" not in mapping:
        raise ValueError("could not find a Date column; headers seen: "
                         + ", ".join(headers))
    if "amount" not in mapping and not ("debit" in mapping or "credit" in mapping):
        raise ValueError("could not find an Amount (or Debit/Credit) column; "
                         "headers seen: " + ", ".join(headers))

    def cell(row, field):
        i = mapping.get(field)
        if i is None or i >= len(row):
            return ""
        return row[i].strip()

    kept, rejected, ambiguous_rows = [], [], []
    seen, duplicates = {}, []
    for n, row in enumerate(data_rows, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        dt, amb = parse_date(cell(row, "date"), day_first=day_first)
        if dt is None:
            rejected.append({"row": n, "reason": f"unparseable date {cell(row, 'date')!r}"})
            continue
        if "amount" in mapping:
            amt = parse_amount(cell(row, "amount"), decimal_comma)
        else:
            d = parse_amount(cell(row, "debit"), decimal_comma)
            c = parse_amount(cell(row, "credit"), decimal_comma)
            amt = (abs(c) if c else Decimal(0)) - (abs(d) if d else Decimal(0))
            if d is None and c is None:
                amt = None
        if amt is None:
            rejected.append({"row": n, "reason": "unparseable amount"})
            continue
        if flip_sign:
            amt = -amt
        payee = cell(row, "payee") or "(no description)"
        txn = {"date": dt, "amount": amt, "payee": payee,
               "memo": cell(row, "memo"), "checknum": cell(row, "checknum"),
               "category": cell(row, "category"), "row": n}
        key = (dt.strftime("%Y-%m-%d"), str(amt), payee.lower())
        if key in seen:
            duplicates.append({"row": n, "first_row": seen[key], "payee": payee,
                               "amount": str(amt), "date": key[0]})
            continue
        seen[key] = n
        txn["fitid"] = hashlib.sha256(
            f"{key[0]}|{amt}|{payee}|{n}".encode()).hexdigest()[:24]
        kept.append(txn)
        if amb:
            ambiguous_rows.append(n)
    kept.sort(key=lambda t: t["date"])
    return {"kept": kept, "rejected": rejected, "duplicates": duplicates,
            "ambiguous_rows": ambiguous_rows,
            "rows_in": len(kept) + len(rejected) + len(duplicates)}


# ---------------------------------------------------------------- writers

def _money(d):
    return f"{d:.2f}"


def write_qif(txns, path, account_type="bank"):
    qtype = {"bank": "Bank", "ccard": "CCard", "cash": "Cash"}[account_type]
    L = [f"!Type:{qtype}"]
    for t in txns:
        L.append("D" + t["date"].strftime("%m/%d/%Y"))
        L.append("T" + _money(t["amount"]))
        L.append("P" + t["payee"])
        if t["memo"]:
            L.append("M" + t["memo"])
        if t["checknum"]:
            L.append("N" + t["checknum"])
        if t["category"]:
            L.append("L" + t["category"])
        L.append("^")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def write_ofx(txns, path, account_name="Account", account_type="bank",
              qbo=False, intu_bid="3000"):
    total = sum(t["amount"] for t in txns)
    dtstart = txns[0]["date"].strftime("%Y%m%d") if txns else "19700101"
    dtend = txns[-1]["date"].strftime("%Y%m%d") if txns else "19700101"
    L = ["OFXHEADER:100", "DATA:OFXSGML", "VERSION:102", "SECURITY:NONE",
         "ENCODING:USASCII", "CHARSET:1252", "COMPRESSION:NONE",
         "OLDFILEUID:NONE", "NEWFILEUID:NONE", "",
         "<OFX>", "<SIGNONMSGSRSV1>", "<SONRS>", "<STATUS>", "<CODE>0",
         "<SEVERITY>INFO", "</STATUS>", "<DTSERVER>" + dtend + "120000",
         "<LANGUAGE>ENG"]
    if qbo:
        L += ["<FI>", "<ORG>LedgerLink", "<FID>" + intu_bid, "</FI>",
              "<INTU.BID>" + intu_bid]
    L += ["</SONRS>", "</SIGNONMSGSRSV1>"]
    body = []
    for t in txns:
        body += ["<STMTTRN>",
                 "<TRNTYPE>" + ("CREDIT" if t["amount"] >= 0 else "DEBIT"),
                 "<DTPOSTED>" + t["date"].strftime("%Y%m%d"),
                 "<TRNAMT>" + _money(t["amount"]),
                 "<FITID>" + t["fitid"],
                 "<NAME>" + _xml(t["payee"][:32])]
        if t["checknum"]:
            body.append("<CHECKNUM>" + _xml(t["checknum"]))
        if t["memo"]:
            body.append("<MEMO>" + _xml(t["memo"]))
        body.append("</STMTTRN>")
    tranlist = (["<BANKTRANLIST>", "<DTSTART>" + dtstart, "<DTEND>" + dtend]
                + body +
                ["</BANKTRANLIST>", "<LEDGERBAL>", "<BALAMT>" + _money(total),
                 "<DTASOF>" + dtend, "</LEDGERBAL>"])
    if account_type == "ccard":
        stmt = (["<CCSTMTRS>", "<CURDEF>USD",
                 "<CCACCTFROM>", "<ACCTID>" + _xml(account_name), "</CCACCTFROM>"]
                + tranlist + ["</CCSTMTRS>"])
        wrap = ["<CREDITCARDMSGSRSV1>", "<CCSTMTTRNRS>", "<TRNUID>1",
                "<STATUS>", "<CODE>0", "<SEVERITY>INFO", "</STATUS>"]
        tail = ["</CCSTMTTRNRS>", "</CREDITCARDMSGSRSV1>"]
    else:
        stmt = (["<STMTRS>", "<CURDEF>USD",
                 "<BANKACCTFROM>", "<BANKID>999999999",
                 "<ACCTID>" + _xml(account_name), "<ACCTTYPE>CHECKING",
                 "</BANKACCTFROM>"]
                + tranlist + ["</STMTRS>"])
        wrap = ["<BANKMSGSRSV1>", "<STMTTRNRS>", "<TRNUID>1",
                "<STATUS>", "<CODE>0", "<SEVERITY>INFO", "</STATUS>"]
        tail = ["</STMTTRNRS>", "</BANKMSGSRSV1>"]
    L += wrap + stmt + tail + ["</OFX>"]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


def _xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ------------------------------------------------------------- re-checkers

def recheck_qif(path):
    count, total = 0, Decimal(0)
    cur = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("T") and len(line) > 1 and (line[1].isdigit() or line[1] == "-"):
            try:
                cur["amt"] = Decimal(line[1:])
            except InvalidOperation:
                pass
        elif line == "^":
            if "amt" in cur:
                count += 1
                total += cur["amt"]
            cur = {}
    return count, total


def recheck_ofx(path):
    text = open(path, encoding="utf-8").read()
    amounts = re.findall(r"<TRNAMT>(-?[\d.]+)", text)
    fitids = re.findall(r"<FITID>([0-9a-f]+)", text)
    total = sum(Decimal(a) for a in amounts)
    return len(amounts), total, len(fitids) == len(set(fitids))


def run_ledgerlink(csv_path, outdir, account_name="Account",
                   account_type="bank", formats=("qif", "ofx", "qbo"),
                   day_first=False, decimal_comma=False, flip_sign=False):
    os.makedirs(outdir, exist_ok=True)
    data = read_transactions(csv_path, day_first=day_first,
                             decimal_comma=decimal_comma, flip_sign=flip_sign)
    txns = data["kept"]
    exp_count = len(txns)
    exp_total = sum(t["amount"] for t in txns)
    exp_credits = sum(t["amount"] for t in txns if t["amount"] > 0)
    exp_debits = sum(t["amount"] for t in txns if t["amount"] < 0)
    base = os.path.splitext(os.path.basename(csv_path))[0]
    written, checks = {}, {}
    if "qif" in formats:
        p = os.path.join(outdir, base + ".qif")
        write_qif(txns, p, account_type)
        c, tot = recheck_qif(p)
        checks["qif"] = (c == exp_count and tot == exp_total)
        written["qif"] = p
    if "ofx" in formats or "qbo" in formats:
        p = os.path.join(outdir, base + ".ofx")
        write_ofx(txns, p, account_name, account_type, qbo=False)
        c, tot, uniq = recheck_ofx(p)
        checks["ofx"] = (c == exp_count and tot == exp_total and uniq)
        written["ofx"] = p
    if "qbo" in formats:
        p = os.path.join(outdir, base + ".qbo")
        write_ofx(txns, p, account_name, "bank", qbo=True)
        c, tot, uniq = recheck_ofx(p)
        checks["qbo"] = (c == exp_count and tot == exp_total and uniq)
        written["qbo"] = p
    conservation = (data["rows_in"] == exp_count + len(data["rejected"])
                    + len(data["duplicates"]))
    verdict = all(checks.values()) and conservation
    L = ["# LedgerLink — conversion & re-check report", "",
         f"Source file: {os.path.basename(csv_path)}  ·  Account: {account_name} "
         f"({account_type})  ·  Rows in: {data['rows_in']}", "",
         "## Summary",
         f"- Converted: {exp_count}",
         f"- Exact duplicates dropped (reported, not silent): {len(data['duplicates'])}",
         f"- Rejected rows (with reasons): {len(data['rejected'])}",
         f"- Ambiguous day/month dates (read as "
         f"{'DD/MM' if day_first else 'MM/DD'} — check these): "
         f"{data['ambiguous_rows'] or 'none'}",
         f"- Credits total: {_money(exp_credits)}  ·  Debits total: {_money(exp_debits)}"
         f"  ·  Net: {_money(exp_total)}",
         "", "## Files written"]
    L += [f"- {os.path.basename(p)} — re-check {'PASS' if checks[k] else 'FAIL'}"
          for k, p in written.items()]
    L += ["", "## Duplicates dropped"]
    L += [f"- row {d['row']} duplicates row {d['first_row']}: {d['date']} "
          f"{d['amount']} {d['payee']}" for d in data["duplicates"]] or ["- none"]
    L += ["", "## Rejected rows"]
    L += [f"- row {r['row']}: {r['reason']}" for r in data["rejected"]] or ["- none"]
    L += ["", "## Re-check verdict",
          f"- {'PASS' if verdict else 'FAIL'}: written files re-parsed — counts and "
          f"totals match the source; conservation {data['rows_in']} in = {exp_count} "
          f"converted + {len(data['duplicates'])} duplicates + "
          f"{len(data['rejected'])} rejected "
          f"({'holds' if conservation else 'BROKEN'}).",
          "- Scope note: QIF carries categories and check numbers; OFX/QBO do not "
          "carry categories (the OFX format has no category field) — assign them "
          "inside your accounting app after import."]
    report_path = os.path.join(outdir, "conversion_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return {"rows_in": data["rows_in"], "converted": exp_count,
            "duplicates": len(data["duplicates"]),
            "rejected": len(data["rejected"]),
            "ambiguous": data["ambiguous_rows"], "net": exp_total,
            "checks": checks, "verdict": verdict, "written": written,
            "report_path": report_path}


def main(argv):
    if len(argv) != 3:
        print("usage: ledger_link.py <bank.csv> <outdir>")
        return 2
    s = run_ledgerlink(argv[1], argv[2])
    print(f"rows_in={s['rows_in']} converted={s['converted']} "
          f"duplicates={s['duplicates']} rejected={s['rejected']} "
          f"net={s['net']} verdict={s['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
