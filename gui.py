#!/usr/bin/env python3
"""LedgerLink desktop shell (tkinter).
Pick a bank CSV -> QIF / OFX / QBO files + conversion report."""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger_link import run_ledgerlink


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LedgerLink — Bank CSV to QIF / OFX / QBO, Re-Checked")
        self.geometry("700x600")
        self.src = tk.StringVar()
        self.account = tk.StringVar(value="Checking")
        self.atype = tk.StringVar(value="bank")
        self.day_first = tk.BooleanVar(value=False)
        self.decimal_comma = tk.BooleanVar(value=False)
        self.flip = tk.BooleanVar(value=False)
        tk.Label(self, text="Bank / card CSV export:").pack(anchor="w", padx=12, pady=(12, 2))
        row = tk.Frame(self)
        row.pack(fill="x", padx=12)
        tk.Entry(row, textvariable=self.src).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse…", command=self.pick).pack(side="left", padx=6)
        tk.Label(self, text="Account name (must match the account in your app):").pack(anchor="w", padx=12, pady=(10, 2))
        tk.Entry(self, textvariable=self.account).pack(fill="x", padx=12)
        tk.Label(self, text="Account type:").pack(anchor="w", padx=12, pady=(10, 2))
        ttk.Combobox(self, textvariable=self.atype, values=["bank", "ccard", "cash"],
                     state="readonly").pack(fill="x", padx=12)
        for var, text in [(self.day_first, "Dates are DD/MM/YYYY (day first)"),
                          (self.decimal_comma, "Amounts use comma decimals (1.234,56)"),
                          (self.flip, "Flip amount signs (credit-card exports)")]:
            tk.Checkbutton(self, text=text, variable=var).pack(anchor="w", padx=12)
        self.btn = tk.Button(self, text="Convert & Re-Check", command=self.run, height=2)
        self.btn.pack(pady=12)
        self.log = scrolledtext.ScrolledText(self, wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.write("LedgerLink converts your bank's CSV into QIF, OFX and QBO files your\n"
                   "accounting app can import — then re-reads the files it wrote and proves\n"
                   "the counts and totals match your CSV. Everything runs locally.\n")

    def write(self, s):
        self.log.insert("end", s)
        self.log.see("end")

    def pick(self):
        p = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if p:
            self.src.set(p)

    def run(self):
        src = self.src.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("LedgerLink", "Choose a CSV file first.")
            return
        self.btn.config(state="disabled")
        threading.Thread(target=self._work, args=(src,), daemon=True).start()

    def _work(self, src):
        try:
            outdir = os.path.join(os.path.dirname(os.path.abspath(src)), "ledgerlink_output")
            s = run_ledgerlink(src, outdir, account_name=self.account.get().strip() or "Account",
                               account_type=self.atype.get(), day_first=self.day_first.get(),
                               decimal_comma=self.decimal_comma.get(), flip_sign=self.flip.get())
            self.write(f"\nDone. {s['rows_in']} rows in → {s['converted']} converted, "
                       f"{s['duplicates']} duplicates dropped, {s['rejected']} rejected "
                       f"(reasons in the report). Net total {s['net']}.\n"
                       f"Re-check verdict: {'PASS' if s['verdict'] else 'FAIL'}\n"
                       f"Files: {', '.join(os.path.basename(p) for p in s['written'].values())}\n"
                       f"Report: {s['report_path']}\n")
        except Exception as e:
            self.write(f"\nError: {e}\n")
        finally:
            self.btn.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
