"""Reconciliation engine: decides, per bank line, which open items it settles, for how much, and the exception.

Stages run in order; each stage only touches bank lines no earlier stage resolved:
  1. batch files   — direct-debit batches, card settlements, cheque deposits, payment runs (exact joins)
  2. returns       — returned direct debits / cheques re-open the collected item (RETURNED)
  3. references    — document numbers quoted in the bank text
  4. party         — payer/beneficiary identified by name/IBAN, amount matched over its open items
  5. no-item       — category from the learned model; receipts nobody explains -> UNIDENTIFIED
Open balances are tracked as lines are applied (chronologically within stages 2-4).
"""
from __future__ import annotations

import bisect
import re
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from bankrec.data import Data
from bankrec.nonitem import ITEM, NonItemModel
from bankrec.text import DocIndex, bank_blob, digits, fold, name_key, norm, tokens

CENT = 0.005
FX_TOL = 0.03                 # bank spread is always well within 3 %
WITHHOLDING_RATES = (0.0125, 0.015, 0.10)
WITHHOLDING_COUNTRIES = {"MX", "BR", "MA"}
MAX_SUBSET_ITEMS = 40


@dataclass
class Row:
    item: str | None
    amount: float | None
    category: str = ""
    exception: str = ""


def _days(a: str, b: str) -> int:
    return (pd.Timestamp(a) - pd.Timestamp(b)).days


class Engine:
    def __init__(self, data: Data, nonitem: NonItemModel | None = None, advices: list[dict] | None = None):
        self.d = data
        self.nonitem = nonitem
        self.advices = advices or []
        it = data.items
        self.items = it
        self.ids = it.item_id.tolist()
        self.kind = it.kind.tolist()
        self.entity = it.entity.tolist()
        self.party = it.party_id.tolist()
        self.ccy = it.currency.tolist()
        self.total = it.total.astype(float).tolist()
        self.open = it.open.astype(float).tolist()
        self.issue = it.issue_date.tolist()
        self.terms = it.payment_terms.tolist()
        self.doc = it.doc.tolist()
        self.pos = {i: k for k, i in enumerate(self.ids)}
        self.docidx = DocIndex(self.doc)
        self.by_party: dict[str, list[int]] = defaultdict(list)
        for k, p in enumerate(self.party):
            self.by_party[p].append(k)
        self.acct_entity = dict(zip(data.bank_accounts.account_id, data.bank_accounts.entity))
        self.result: dict[str, list[Row]] = {}
        self.how: dict[str, str] = {}
        self._terms = {r.code: (r.early_payment_discount_pct or 0, r.discount_days or 0)
                       for r in data.payment_terms.itertuples()}
        self._fx = self._fx_table()
        self._parties()
        self._advice_index()

    # ------------------------------------------------------------------ helpers
    def _fx_table(self):
        fx = defaultdict(lambda: ([], []))
        for r in self.d.fx.sort_values("date").itertuples():
            dates, rates = fx[r.currency]
            dates.append(r.date)
            rates.append(r.units_per_eur)
        return fx

    def rate(self, ccy: str, date: str) -> float:
        if ccy == "EUR":
            return 1.0
        dates, rates = self._fx.get(ccy, ([], []))
        if not dates:
            return float("nan")
        i = bisect.bisect_right(dates, date) - 1
        return rates[max(i, 0)]

    def convert(self, amount: float, src: str, dst: str, date: str) -> float:
        if src == dst:
            return amount
        return amount / self.rate(src, date) * self.rate(dst, date)

    def _parties(self):
        d = self.d
        cu = d.customers
        self.cust_country = dict(zip(cu.customer_id, cu.country))
        self.parent_children: dict[str, list[str]] = defaultdict(list)
        for c, p in zip(cu.customer_id, cu.parent_customer_id):
            if p:
                self.parent_children[p].append(c)
        self.mandate_party = {m: c for m, c in zip(cu.sepa_mandate_id, cu.customer_id) if m}
        self.iban_party: dict[str, str] = {}
        for df, idcol in ((cu, "customer_id"), (d.suppliers, "supplier_id")):
            for i, p in zip(df.iban, df[idcol]):
                if i:
                    self.iban_party[norm(i)] = p
        # name index per entity: word -> parties
        names: dict[str, set[str]] = defaultdict(set)
        for df, idcol, cols in ((cu, "customer_id", ("name", "trade_name")), (d.suppliers, "supplier_id", ("name",))):
            for r in df.to_dict("records"):
                for c in cols:
                    if r.get(c):
                        names[r[idcol]].add(name_key(r[c]))
        for p, n in zip(self.party, self.items.party_name):
            if n:
                names[p].add(name_key(n))
        self.party_names = names
        self.party_entity = {**dict(zip(cu.customer_id, cu.entity)), **dict(zip(d.suppliers.supplier_id, d.suppliers.entity))}
        self.word_parties: dict[tuple[str, str], set[str]] = defaultdict(set)
        for p, ns in names.items():
            e = self.party_entity.get(p, "")
            for n in ns:
                for w in n.split():
                    if len(w) >= 3:
                        self.word_parties[(e, w)].add(p)

    # --------------------------------------------------------------- advices
    def _advice_index(self) -> None:
        self.adv_by_ref: dict[str, list[int]] = defaultdict(list)
        self.adv_by_amt: dict[int, list[int]] = defaultdict(list)
        self.adv_used: set[int] = set()
        for i, a in enumerate(self.advices):
            for r in a["refs"]:
                n = norm(r)
                if len(n) >= 6:
                    self.adv_by_ref[n].append(i)
            paid = [ln["nums"][-1] for ln in a["lines"] if self._adv_doc(a, ln) is not None]
            for v in {a["total"], round(sum(paid), 2) if paid else None}:
                if v:
                    self.adv_by_amt[round(v * 100)].append(i)

    def _adv_doc(self, a: dict, ln: dict) -> int | None:
        """Item of an advice line: our document number in any of the line's tokens (prefix optional)."""
        best = None
        for tok in ln["tokens"]:
            for k in self.docidx.lookup(norm(tok)):
                if self.kind[k][:2] != "AR":
                    continue
                if self.entity[k] == a["entity"]:
                    return k
                best = best if best is not None else k
        return best

    def find_advice(self, txn: dict, blob: str) -> int | None:
        amt = round(float(txn["amount"]) * 100)
        cands = []
        btoks = set(tokens(blob))
        for tok in btoks:
            cands.extend(self.adv_by_ref.get(tok, ()))
        if not cands:  # truncated reference in the bank text: prefix of an advice ref, same amount
            for i in self.adv_by_amt.get(amt, ()):
                if any(len(t) >= 7 and any(norm(r).startswith(t) or t.startswith(norm(r)) for r in self.advices[i]["refs"])
                       for t in btoks):
                    cands.append(i)
        if not cands:  # no reference: same entity, same amount, payer named in the text, close in time
            parties = None
            for i in self.adv_by_amt.get(amt, ()):
                a = self.advices[i]
                if a["entity"] != txn["entity"] or not a["received"]:
                    continue
                if not -10 <= _days(a["received"], txn["booking_date"]) <= 20:
                    continue
                if parties is None:
                    parties = self.identify_party(txn, blob)
                pa = self.identify_party({"entity": a["entity"], "counterparty_name": a["payer"]}, fold(a["payer"]))
                if parties & pa or not parties:
                    cands.append(i)
        cands = [i for i in dict.fromkeys(cands) if i not in self.adv_used and self.advices[i]["entity"] == txn["entity"]]
        if not cands:
            return None
        return min(cands, key=lambda i: (abs(round((self.advices[i]["total"] or 0) * 100) - amt),
                                         abs(_days(self.advices[i]["received"] or txn["booking_date"], txn["booking_date"]))))

    def advice_rows(self, txn: dict, a: dict) -> list[Row]:
        rows, seen = [], set()
        fx = False
        for ln in a["lines"]:
            k = self._adv_doc(a, ln)
            if k is None or k in seen:
                continue
            seen.add(k)
            nums = ln["nums"]
            paid, gross = abs(nums[-1]), abs(nums[0])
            open_ = self.open[k] if self.open[k] > CENT else self.total[k]
            fx = self.ccy[k] != txn["currency"]
            if self.is_credit(k):
                rows.append(Row(self.ids[k], round(open_, 2), "", ""))
                continue
            tol = max(0.01, 0.005 * open_)
            deduction = len(nums) >= 3 and abs(nums[1]) > 0.005
            reason = ln["reason"] and any(w not in " ".join(ln["tokens"]) for w in ln["reason"].split())
            if abs(paid - open_) <= tol:
                rows.append(Row(self.ids[k], round(open_, 2), "", "FX_DIFFERENCE" if fx else ""))
            elif paid > open_:
                rows.append(Row(self.ids[k], round(open_, 2), "", "FX_DIFFERENCE" if fx else "OVERPAYMENT"))
            elif deduction or reason:
                rows.append(Row(self.ids[k], round(paid, 2), "", "SHORT_DISPUTE"))
            else:
                exc = self.short_reason(txn, k, open_ - paid, paid, fx)
                if exc == "PARTIAL":
                    rows.append(Row(self.ids[k], round(paid, 2), "", "PARTIAL"))
                else:
                    rows.append(Row(self.ids[k], round(open_, 2), "", "FX_DIFFERENCE" if fx else exc))
        return rows

    def try_advice(self, txn: dict, blob: str) -> bool:
        if not self.advices or float(txn["amount"]) <= 0:
            return False
        i = self.find_advice(txn, blob)
        if i is None:
            return False
        rows = self.advice_rows(txn, self.advices[i])
        if not rows:
            return False
        self.adv_used.add(i)
        self.emit(txn["bank_txn_id"], rows, "advice")
        return True

    def is_credit(self, k: int) -> bool:
        return self.kind[k] in ("ARC", "APC")

    def side_ok(self, k: int, amount: float) -> bool:
        """AR items are settled by receipts, AP items by payments."""
        return self.kind[k].startswith("AR") == (amount > 0)

    def emit(self, t: str, rows: list[Row], how: str) -> None:
        self.result[t] = rows
        self.how[t] = how
        for r in rows:
            if r.item:
                k = self.pos[r.item]
                if r.exception == "RETURNED":
                    self.open[k] += r.amount
                elif r.exception in ("", "FX_DIFFERENCE", "SHORT_DISCOUNT", "SHORT_WITHHOLDING",
                                     "SHORT_BANK_CHARGES", "OVERPAYMENT"):
                    self.open[k] = 0.0
                else:
                    self.open[k] = max(0.0, self.open[k] - r.amount)

    # --------------------------------------------------------------- settlement
    def settle(self, txn: dict, ks: list[int], force_single: bool = False) -> list[Row] | None:
        """Rows if cash explains items `ks` (invoices + netted credit notes) under POLICIES §2-3, else None."""
        if not ks:
            return None
        ccys = {self.ccy[k] for k in ks}
        if len(ccys) != 1:
            return None
        iccy = ccys.pop()
        acct_ccy = txn["currency"]
        date = txn["booking_date"]
        fx = iccy != acct_ccy
        if fx and txn.get("orig_currency") == iccy and txn.get("orig_amount") == txn.get("orig_amount"):
            cash = abs(float(txn["orig_amount"]))
        else:
            cash = abs(self.convert(float(txn["amount"]), acct_ccy, iccy, date)) if fx else abs(float(txn["amount"]))
        inv = [k for k in ks if not self.is_credit(k)]
        crn = [k for k in ks if self.is_credit(k)]
        owed = sum(self.open[k] for k in inv) - sum(self.open[k] for k in crn)
        if owed <= 0 or not inv:
            return None
        diff = owed - cash
        full_exc = "FX_DIFFERENCE" if fx else ""
        tol = max(0.01, FX_TOL * owed) if fx else 0.01

        def rows(inv_exc: dict[int, tuple[float, str]] | None = None) -> list[Row]:
            out = []
            for k in sorted(ks, key=lambda k: self.ids[k]):
                if inv_exc and k in inv_exc:
                    a, e = inv_exc[k]
                else:
                    a, e = round(self.open[k], 2), ("" if self.is_credit(k) else full_exc)
                out.append(Row(self.ids[k], a, "", e))
            return out

        if abs(diff) <= tol:
            return rows()
        if len(inv) != 1 and not force_single:
            return None
        k = inv[0]
        if diff < 0:  # paid more than owed
            if len(inv) == 1:
                return rows({k: (round(self.open[k], 2), "FX_DIFFERENCE" if fx else "OVERPAYMENT")})
            return None
        # short payment on a single invoice
        exc = self.short_reason(txn, k, diff, cash + sum(self.open[c] for c in crn), fx)
        if exc == "PARTIAL":
            paid = round(self.open[k] - diff, 2)
            return rows({k: (paid, "PARTIAL")}) if paid > 0 else None
        return rows({k: (round(self.open[k], 2), "FX_DIFFERENCE" if fx else exc)})

    def short_reason(self, txn: dict, k: int, diff: float, paid_for_k: float, fx: bool) -> str:
        total = self.total[k]
        pct, ddays = self._terms.get(self.terms[k], (0, 0))
        if pct and abs(paid_for_k - (total - round(total * pct / 100, 2))) <= 0.02:
            if _days(txn["booking_date"], self.issue[k]) <= ddays + 3:
                return "SHORT_DISCOUNT"
            return "PARTIAL"
        if self.cust_country.get(self.party[k]) in WITHHOLDING_COUNTRIES:
            if any(abs(diff - round(total * r, 2)) <= 0.02 for r in WITHHOLDING_RATES):
                return "SHORT_WITHHOLDING"
        diff_eur = self.convert(diff, self.ccy[k], "EUR", txn["booking_date"])
        if diff_eur <= 60 and diff <= 0.02 * total and _foreign(txn):
            return "SHORT_BANK_CHARGES"
        return "PARTIAL"

    # ------------------------------------------------------------- candidates
    def open_of(self, parties: set[str], amount: float) -> list[int]:
        """Open invoices and credit notes of these parties on the side the line settles (AR receipts, AP payments)."""
        side = "AR" if amount > 0 else "AP"
        out = []
        for p in parties:
            for k in self.by_party.get(p, ()):
                if self.open[k] > CENT and self.kind[k][:2] == side:
                    out.append(k)
        return sorted(set(out), key=lambda k: (self.issue[k], self.ids[k]))

    def party_item(self, parties: set[str], side: str, amount: float, ref_digits: str = "") -> int | None:
        """One item of these parties: number ending in ref_digits, else the oldest open one with this amount."""
        ks = [k for p in parties for k in self.by_party.get(p, ()) if self.kind[k][:2] == side]
        if ref_digits and len(ref_digits) >= 4:
            hit = [k for k in ks if digits(self.doc[k]).endswith(ref_digits)]
            if hit:
                return min(hit, key=lambda k: (self.open[k] <= CENT, self.issue[k]))
        hit = [k for k in ks if abs(self.open[k] - amount) <= 0.01] or [k for k in ks if abs(self.total[k] - amount) <= 0.01]
        return min(hit, key=lambda k: self.issue[k]) if hit else None

    def ref_items(self, txn: dict, blob: str) -> list[int]:
        ent = txn["entity"]
        amount = float(txn["amount"])
        side = "AR" if amount > 0 else "AP"
        found, seen = [], set()
        for tok in tokens(blob):
            for k in self.docidx.lookup(tok):
                if k in seen or self.kind[k][:2] != side:
                    continue
                if self.entity[k] != ent and not (ent == "ES00" and side == "AP"):
                    continue
                seen.add(k)
                found.append(k)
        return found

    def identify_party(self, txn: dict, blob: str) -> set[str]:
        acct = norm(txn.get("counterparty_account", ""))
        if acct and acct in self.iban_party:
            return {self.iban_party[acct]}
        ent = txn["entity"]
        src = fold(txn.get("counterparty_name", "")) or blob
        words = [w for w in re.split(r"[^A-Z0-9]+", src) if len(w) >= 3]
        score: dict[str, int] = defaultdict(int)
        ents = {ent, "ES01", "ES02", "PT01"} if ent == "ES00" else {ent}
        for w in set(words):
            for e in ents:
                for p in self.word_parties.get((e, w), ()):
                    score[p] += 1
        if not score:
            return set()
        best = max(score.values())
        cands = {p for p, s in score.items() if s == best}
        # require the match to cover most of the party's name
        good = set()
        for p in cands:
            nw = min(len(n.split()) for n in self.party_names[p] if n) if self.party_names[p] else 99
            if best >= min(2, nw):
                good.add(p)
        return good if len(good) <= 3 else set()

    def subset(self, txn: dict, ks: list[int]) -> list[int] | None:
        """Items of ks (oldest first) whose net open amount equals the cash; FIFO preference."""
        target = round(abs(float(txn["amount"])) * 100)
        if txn["currency"] != (self.ccy[ks[0]] if ks else txn["currency"]):
            return None
        sgn = [(-1 if self.is_credit(k) else 1) for k in ks]
        vals = [round(self.open[k] * 100) * s for k, s in zip(ks, sgn)]
        for k, v in zip(ks, vals):               # single exact, oldest first
            if v == target:
                return [k]
        run = 0
        for i, v in enumerate(vals):             # oldest-first prefix
            run += v
            if run == target:
                return ks[: i + 1]
        if len(ks) > MAX_SUBSET_ITEMS:
            ks, vals = ks[:MAX_SUBSET_ITEMS], vals[:MAX_SUBSET_ITEMS]
        neg = -sum(v for v in vals if v < 0)
        hi = target + neg                      # sums above this can never come back down to target
        back: dict[int, tuple[int, int]] = {0: (0, -1)}   # sum -> (previous sum, item index)
        for i, v in enumerate(vals):
            for s in list(back):
                ns = s + v
                if ns not in back and -neg <= ns <= hi:
                    back[ns] = (s, i)
            if target in back:
                out, s = [], target
                while True:
                    prev, j = back[s]
                    if j == -1:
                        return out[::-1]
                    out.append(ks[j])
                    s = prev
            if len(back) > 150_000:
                break
        return None

    # ------------------------------------------------------------------ stages
    def run(self) -> None:
        self.stage_batches()
        txns = self.d.txns.sort_values(["booking_date", "bank_txn_id"]).to_dict("records")
        for txn in txns:
            if txn["bank_txn_id"] in self.result:
                continue
            self.match_line(txn)
        for txn in txns:
            if txn["bank_txn_id"] not in self.result:
                self.no_item(txn)

    def match_line(self, txn: dict) -> None:
        t = txn["bank_txn_id"]
        amount = float(txn["amount"])
        blob = bank_blob(txn)
        if amount < 0 and self.try_return(txn, blob):
            return
        if self.try_advice(txn, blob):
            return
        if self.nonitem:
            label, share, n = self.nonitem.predict(txn)
            if label != ITEM and share >= 0.9 and n >= 20:
                self.emit(t, [Row(None, None, label, "UNIDENTIFIED" if label == "UNIDENTIFIED" else "")], "nonitem")
                return
        refs = [k for k in self.ref_items(txn, blob)]
        if refs:
            open_refs = [k for k in refs if self.open[k] > CENT]
            if open_refs:
                r = self.settle(txn, open_refs)
                if r is None:
                    sub = self.subset(txn, open_refs)
                    r = self.settle(txn, sub) if sub else None
                if r is None:
                    parties = {self.party[k] for k in open_refs}
                    extra = [k for k in self.open_of(parties, amount) if self.is_credit(k)]
                    if extra:
                        r = self.settle(txn, open_refs + extra)
                if r is None and len(open_refs) == 1:
                    r = self.settle(txn, open_refs, force_single=True)
                if r is not None:
                    self.emit(t, r, "ref")
                    return
            paid = [k for k in refs if self.open[k] <= CENT and abs(self.total[k] - abs(amount)) <= 0.01]
            if paid:
                self.emit(t, [Row(self.ids[paid[0]], round(abs(amount), 2), "", "DUPLICATE_PAYMENT")], "dup")
                return
        parties = self.identify_party(txn, blob)
        if parties:
            fam = set(parties)
            for p in parties:
                fam.update(self.parent_children.get(p, ()))
            cands = self.open_of(fam, amount)
            if cands:
                sub = self.subset(txn, cands)
                r = self.settle(txn, sub) if sub else None
                if r is None:
                    r = self.settle_fx(txn, cands)
                if r is not None:
                    self.emit(t, r, "party")
                    return

    def settle_fx(self, txn: dict, cands: list[int]) -> list[Row] | None:
        """Different-currency line: find the item set whose amount equals the instructed (original) amount."""
        oc = txn.get("orig_currency")
        if not oc or txn.get("orig_amount") != txn.get("orig_amount"):
            return None
        same = [k for k in cands if self.ccy[k] == oc]
        if not same:
            return None
        fake = dict(txn, amount=float(txn["orig_amount"]) * (1 if float(txn["amount"]) > 0 else -1), currency=oc)
        sub = self.subset(fake, same)
        return self.settle(txn, sub) if sub else None

    def try_return(self, txn: dict, blob: str) -> bool:
        """Returned direct debit (mandate) or cheque: re-open the collected item."""
        if not re.search(r"DEVOLUCION RECIBO|RUECKLASTSCHRIFT|IMPAYE|RETURN|R-TRANS|CHARGEBACK|RETOUR|DEVUELTO|"
                         r"UNPAID|IMPAGADO|RECHAZ", blob):
            return False
        amount = abs(float(txn["amount"]))
        t = txn["bank_txn_id"]
        m = re.search(r"VTM-[A-Z]{2}-\d+", blob)
        parties = set()
        if m and m.group(0) in self.mandate_party:
            parties.add(self.mandate_party[m.group(0)])
        if not parties:
            parties = self.identify_party(txn, blob)
        for k in self.ref_items(dict(txn, amount=1.0), blob):
            if abs(self.total[k] - amount) <= 0.01:
                self.emit(t, [Row(self.ids[k], round(amount, 2), "", "RETURNED")], "return")
                return True
        best = None
        for p in parties:
            for k in self.by_party.get(p, ()):
                if self.kind[k] == "ARI" and abs(self.total[k] - amount) <= 0.01 and self.issue[k] <= txn["booking_date"]:
                    if best is None or self.issue[k] > self.issue[best]:
                        best = k
        if best is not None:
            self.emit(t, [Row(self.ids[best], round(amount, 2), "", "RETURNED")], "return")
            return True
        return False

    def no_item(self, txn: dict) -> None:
        t = txn["bank_txn_id"]
        amount = float(txn["amount"])
        cat = self.nonitem.best_category(txn) if self.nonitem else None
        if amount > 0:
            label = None
            if self.nonitem:
                lab, share, n = self.nonitem.predict(txn)
                if lab not in (ITEM, "UNIDENTIFIED") and share >= 0.5:
                    label = lab
            if label:
                self.emit(t, [Row(None, None, label, "")], "nonitem")
            else:
                self.emit(t, [Row(None, None, "UNIDENTIFIED", "UNIDENTIFIED")], "unidentified")
        else:
            cat = cat if cat and cat != "UNIDENTIFIED" else "BANK_FEES"
            self.emit(t, [Row(None, None, cat, "")], "nonitem")

    # ----------------------------------------------------------- batch files
    def stage_batches(self) -> None:
        tx = self.d.txns
        blobs = {r["bank_txn_id"]: bank_blob(r) for r in tx.to_dict("records")}
        by_acct_amt: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for r in tx.to_dict("records"):
            by_acct_amt[(r["account_id"], round(float(r["amount"]) * 100))].append(r)

        def find(acct: str, amount: float, date: str, ids: list[str], days: int = 7) -> str | None:
            cands = [r for r in by_acct_amt.get((acct, round(amount * 100)), ())
                     if r["bank_txn_id"] not in self.result and abs(_days(r["booking_date"], date)) <= days]
            if not cands:
                return None
            for r in cands:
                b = blobs[r["bank_txn_id"]]
                if any(i and i in b for i in ids):
                    return r["bank_txn_id"]
            return min(cands, key=lambda r: abs(_days(r["booking_date"], date)))["bank_txn_id"]

        def lookup_doc(ref: str, entity: str, side: str, amount: float | None = None, party_hint: str = "") -> int | None:
            best, best_score = None, -1
            for tok in [norm(ref)] + [norm(x) for x in re.split(r"[\s,;]+", fold(ref)) if x]:
                for k in self.docidx.lookup(tok):
                    if self.kind[k][:2] != side:
                        continue
                    s = 0
                    s += 4 if self.entity[k] == entity else 0
                    s += 2 if amount is not None and abs(self.total[k] - amount) <= 0.01 else 0
                    s += 2 if amount is not None and abs(self.open[k] - amount) <= 0.01 else 0
                    s += 1 if party_hint and name_key(party_hint) in self.party_names.get(self.party[k], ()) else 0
                    s += 1 if self.open[k] > CENT else 0
                    if s > best_score:
                        best, best_score = k, s
                if best is not None:
                    return best
            return best

        # direct-debit batches: batch line = batch_total on the account
        dd = self.d.dd
        if len(dd):
            for bid, g in dd.groupby("batch_id", sort=False):
                f = g.iloc[0]
                t = find(f.account_id, float(f.batch_total), f.collection_date, [bid])
                if not t:
                    continue
                ent = self.acct_entity.get(f.account_id, "")
                rows = []
                for r in g.itertuples():
                    k = lookup_doc(r.remittance_info, ent, "AR", float(r.amount), r.debtor_name)
                    if k is None:
                        p = self.mandate_party.get(r.mandate_id)
                        parties = {p} if p else self.identify_party({"entity": ent, "counterparty_name": r.debtor_name},
                                                                     fold(r.debtor_name))
                        k = self.party_item(parties, "AR", float(r.amount), digits(r.remittance_info))
                    if k is not None:
                        rows.append(Row(self.ids[k], round(float(r.amount), 2), "", ""))
                if rows:
                    self.emit(t, _dedup(rows), "dd_batch")
        # card settlements: line = sum of net per settlement
        cards = self.d.cards
        if len(cards):
            for sid, g in cards.groupby("settlement_id", sort=False):
                f = g.iloc[0]
                net = round(g.net.sum(), 2)
                t = find(f.account_id, net, f.settlement_date, [str(sid), str(f.merchant_id)], days=5)
                if not t:
                    continue
                ent = self.acct_entity.get(f.account_id, "")
                rows = []
                for r in g.itertuples():
                    k = lookup_doc(r.order_number, ent, "AR", float(r.gross))
                    if k is not None:
                        rows.append(Row(self.ids[k], round(float(r.gross), 2), "", ""))
                if rows:
                    self.emit(t, _dedup(rows), "card")
        # cheque deposits
        ch = self.d.cheques
        if len(ch):
            for did, g in ch.groupby("deposit_id", sort=False):
                f = g.iloc[0]
                t = find(f.account_id, float(f.deposit_total), f.deposit_date, [str(did)])
                if not t:
                    continue
                ent = self.acct_entity.get(f.account_id, "")
                rows = []
                for r in g.itertuples():
                    k = lookup_doc(r.stub_reference, ent, "AR", float(r.amount), r.drawer)
                    if k is None:
                        parties = self.identify_party({"entity": ent, "counterparty_name": r.drawer}, fold(r.drawer))
                        k = self.party_item(parties, "AR", float(r.amount), digits(r.stub_reference))
                    if k is not None:
                        rows.append(Row(self.ids[k], round(float(r.amount), 2), "", ""))
                if rows:
                    self.emit(t, _dedup(rows), "cheque")
        # payment runs: transfers -> one debit per end_to_end_id; aggregated/confirming -> one per run
        pr = self.d.payruns
        if len(pr):
            def payrun_rows(g: pd.DataFrame, acct: str) -> list[Row]:
                side_ent = self.acct_entity.get(acct, "")
                acct_ccy = acct.rsplit("-", 2)[-1] if acct.count("-") >= 2 else ""
                rows = []
                for r in g.itertuples():
                    amt = abs(float(r.document_amount))
                    k = lookup_doc(r.document_number, side_ent, "AP", amt, r.beneficiary)
                    if k is None:
                        ib = norm(r.beneficiary_iban)
                        parties = {self.iban_party[ib]} if ib in self.iban_party else self.identify_party(
                            {"entity": side_ent, "counterparty_name": r.beneficiary}, fold(r.beneficiary))
                        k = self.party_item(parties, "AP", amt, digits(r.document_number))
                    if k is None:
                        continue
                    exc = ""
                    if not self.is_credit(k):
                        if amt < self.open[k] - max(0.01, 0.005 * self.open[k]):
                            exc = "PARTIAL"
                        elif self.ccy[k] != r.currency or (acct_ccy and self.ccy[k] != acct_ccy):
                            exc = "FX_DIFFERENCE"
                    rows.append(Row(self.ids[k], round(amt, 2), "", exc))
                return _dedup(rows)

            plain = pr[pr.run_type != "confirming"]
            plain = plain.assign(_key=plain.end_to_end_id.where(plain.run_type == "transfers", plain.run_id))
            for key, g in plain.groupby("_key", sort=False):
                f = g.iloc[0]
                total = float(f.transfer_amount) if f.run_type == "transfers" else round(
                    g.drop_duplicates("end_to_end_id").transfer_amount.sum(), 2)
                t = find(f.debtor_account_id, -total, f.execution_date, [str(key), str(f.end_to_end_id)])
                if t:
                    rows = payrun_rows(g, f.debtor_account_id)
                    if rows:
                        self.emit(t, rows, "payrun")
            self.stage_confirming(pr[pr.run_type == "confirming"], blobs, payrun_rows)

    def stage_confirming(self, conf: pd.DataFrame, blobs: dict, payrun_rows) -> None:
        """Confirming run: the bank debits it per maturity date, each debit paying N beneficiaries of the run."""
        if not len(conf):
            return
        runs = {rid: g for rid, g in conf.groupby("run_id", sort=False)}
        used: set[str] = set()
        lines = self.d.txns[self.d.txns.amount < 0].sort_values("booking_date")
        for txn in lines.to_dict("records"):
            t = txn["bank_txn_id"]
            if t in self.result:
                continue
            b = blobs[t]
            rid = next((r for r in re.findall(r"CF\d{10,14}", b) if r in runs), None)
            if not rid:
                continue
            m = re.search(rid + r"\s+(\d+)\s", b)
            n = int(m.group(1)) if m else None
            g = runs[rid]
            groups = [(e2e, round(float(gg.transfer_amount.iloc[0]) * 100)) for e2e, gg in g.groupby("end_to_end_id")
                      if e2e not in used]
            pick = _subset_count([v for _, v in groups], round(-float(txn["amount"]) * 100), n)
            if pick is None:
                continue
            chosen = {groups[i][0] for i in pick}
            used |= chosen
            rows = payrun_rows(g[g.end_to_end_id.isin(chosen)], txn["account_id"])
            if rows:
                self.emit(t, rows, "confirming")

    def submission_rows(self) -> list[tuple]:
        out = []
        for t in self.d.txns.bank_txn_id:
            for r in self.result.get(t, [Row(None, None, "UNIDENTIFIED", "UNIDENTIFIED")]):
                out.append((t, r.item, r.amount, r.category if not r.item else "", r.exception))
        return out


def _foreign(txn: dict) -> bool:
    b = fold(txn.get("text", "")) + " " + txn.get("bank_code", "")
    return bool(re.search(r"DIV\.|EN DIV|XBCT|SWIFT|FOREIGN|AUSLAND|ETRANGER|INTERNATIONAL|^13", b)) or \
        txn.get("bank_code", "") == "13" or bool(txn.get("orig_currency"))


def _subset_count(vals: list[int], target: int, n: int | None, cap: int = 300_000) -> list[int] | None:
    """Indexes of a subset of vals summing to target (exactly n elements when n is given)."""
    reach: dict[tuple[int, int], tuple[int, ...]] = {(0, 0): ()}
    for i, v in enumerate(vals):
        new = {}
        for (s, c), path in reach.items():
            if n is not None and c >= n:
                continue
            key = (s + v, c + 1)
            if s + v <= target and key not in reach and key not in new:
                new[key] = path + (i,)
        reach.update(new)
        if (target, n) in reach if n is not None else any(s == target and c for s, c in reach):
            break
        if len(reach) > cap:
            break
    if n is not None:
        return list(reach[(target, n)]) if (target, n) in reach else None
    hit = [p for (s, c), p in reach.items() if s == target and c]
    return list(min(hit, key=len)) if hit else None


def _dedup(rows: list[Row]) -> list[Row]:
    seen, out = set(), []
    for r in rows:
        if r.item in seen:
            continue
        seen.add(r.item)
        out.append(r)
    return out
