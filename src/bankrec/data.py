"""Locate and load the challenge data.

The archives can be extracted in any layout: phase folders are found by content (a folder named
`sample|train|live|final` that holds `bank_transactions.csv.gz`), masters by `categories.csv`.
"""
from __future__ import annotations

import glob
import re
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

PHASES = ("sample", "train", "live", "final")


def read(path: Path | str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def find_phase_dir(root: Path, phase: str) -> Path:
    hits = [Path(p).parent for p in glob.glob(str(root / "**" / "bank_transactions.csv.gz"), recursive=True)
            if Path(p).parent.name == phase]
    if not hits:
        raise FileNotFoundError(f"no {phase}/bank_transactions.csv.gz under {root}")
    return sorted(hits, key=lambda p: len(p.parts))[0]


def find_masters(root: Path, phase: str) -> Path:
    if phase == "final":  # FINAL ships updated masters
        hits = glob.glob(str(root / "**" / "masters_final" / "categories.csv"), recursive=True)
        if hits:
            return Path(hits[0]).parent
    hits = [Path(p).parent for p in glob.glob(str(root / "**" / "categories.csv"), recursive=True)
            if Path(p).parent.name == "masters"]
    if not hits:
        raise FileNotFoundError(f"no masters/categories.csv under {root}")
    return hits[0]


def _num(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


_YM = re.compile(r"_(\d{6})(?:_\d+)?\.csv$")
LOOKBACK_MONTHS = 4  # batch files of earlier months still settle lines of this phase (confirming maturities…)


def _concat_dirs(dirs: list[Path], min_ym: str = "") -> pd.DataFrame:
    """Batch files of the phase dir plus earlier phases (only months >= min_ym); same file name read once."""
    seen, frames = set(), []
    for d in dirs:
        for f in sorted(glob.glob(str(d / "**" / "*.csv"), recursive=True)):
            name = os.path.basename(f)
            if name.startswith("._") or name == "index.csv" or name in seen:
                continue
            m = _YM.search(name)
            if d != dirs[0] and (not m or m.group(1) < min_ym):
                continue
            seen.add(name)
            frames.append(read(f).assign(source_file=name))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@dataclass
class Data:
    phase: str
    phase_dir: Path
    masters_dir: Path
    txns: pd.DataFrame
    items: pd.DataFrame          # one row per open item, columns below (see _items)
    customers: pd.DataFrame
    suppliers: pd.DataFrame
    categories: list[str]
    payment_terms: pd.DataFrame
    fx: pd.DataFrame
    bank_accounts: pd.DataFrame
    entities: pd.DataFrame
    dd: pd.DataFrame = field(default_factory=pd.DataFrame)
    cards: pd.DataFrame = field(default_factory=pd.DataFrame)
    cheques: pd.DataFrame = field(default_factory=pd.DataFrame)
    payruns: pd.DataFrame = field(default_factory=pd.DataFrame)
    advices_index: pd.DataFrame = field(default_factory=pd.DataFrame)
    pdf_index: pd.DataFrame = field(default_factory=pd.DataFrame)


ITEM_COLS = ["item_id", "kind", "entity", "party_id", "party_name", "doc", "issue_date", "due_date", "currency",
             "total", "open", "payment_terms", "po", "payment_method", "related_document", "reason"]


def _items(d: Path, extra_dirs: list[Path]) -> pd.DataFrame:
    """Items issued in this phase (+ open_items_at_start for LIVE/FINAL). `open` = open amount at phase start."""
    parts = []
    ar = read(d / "ar_invoices.csv.gz")
    parts.append(pd.DataFrame({"item_id": ar.item_id, "kind": "ARI", "entity": ar.entity, "party_id": ar.customer_id,
                               "party_name": ar.customer_name, "doc": ar.document_number, "issue_date": ar.issue_date,
                               "due_date": ar.due_date, "currency": ar.currency, "total": ar.total_amount,
                               "payment_terms": ar.payment_terms, "po": ar.customer_po,
                               "payment_method": ar.payment_method}))
    ap = read(d / "ap_invoices.csv.gz")
    parts.append(pd.DataFrame({"item_id": ap.item_id, "kind": "API", "entity": ap.entity, "party_id": ap.supplier_id,
                               "party_name": ap.supplier_name, "doc": ap.supplier_invoice_number,
                               "issue_date": ap.issue_date, "due_date": ap.due_date, "currency": ap.currency,
                               "total": ap.total_amount, "payment_terms": ap.payment_terms, "po": ap.our_po,
                               "payment_method": ap.payment_method}))
    cn = read(d / "credit_notes.csv.gz")
    parts.append(pd.DataFrame({"item_id": cn.item_id, "kind": cn.item_id.str[:3], "entity": cn.entity,
                               "party_id": cn.party_id, "party_name": cn.party_name, "doc": cn.document_number,
                               "issue_date": cn.issue_date, "currency": cn.currency, "total": cn.total_amount,
                               "related_document": cn.related_document, "reason": cn.reason}))
    items = pd.concat(parts, ignore_index=True)
    items["open"] = items["total"]
    start = d / "open_items_at_start.csv.gz"
    if start.exists():
        o = read(start)
        old = pd.DataFrame({"item_id": o.item_id, "kind": o.item_type, "entity": o.entity, "party_id": o.party_id,
                            "doc": o.document_number, "issue_date": o.issue_date, "due_date": o.due_date,
                            "currency": o.currency, "total": o.total_amount, "open": o.open_amount})
        old = old[~old.item_id.isin(items.item_id)]
        # enrich carried-over items (names, terms, po…) from earlier phases' item files
        if extra_dirs:
            prev = pd.concat([_items(p, []) for p in extra_dirs], ignore_index=True).drop_duplicates("item_id", keep="last")
            # recently issued items already settled before the phase: open 0, kept so a duplicate payment of
            # one of them can still be recognised (DUPLICATE_PAYMENT)
            start = min(items.issue_date.min(), o.issue_date.max())
            floor = (pd.Timestamp(start) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
            closed = prev[(prev.issue_date >= floor) & ~prev.item_id.isin(old.item_id) & ~prev.item_id.isin(items.item_id)]
            closed = closed.assign(open=0.0)
            prev = prev.set_index("item_id")
            for c in ("party_name", "payment_terms", "po", "payment_method", "related_document", "reason"):
                old[c] = old.item_id.map(prev[c]) if c in prev else ""
            old = pd.concat([old, closed], ignore_index=True)
        items = pd.concat([items, old], ignore_index=True)
    items = items.reindex(columns=ITEM_COLS).fillna("")
    items = _num(items, ["total", "open"])
    return items.drop_duplicates("item_id", keep="first").reset_index(drop=True)


def load(root: Path | str, phase: str) -> Data:
    root = Path(root)
    d = find_phase_dir(root, phase)
    m = find_masters(root, phase)
    earlier = {"sample": [], "train": [], "live": ["train"], "final": ["train", "live"]}[phase]
    extra = [find_phase_dir(root, p) for p in earlier]
    txns = _num(read(d / "bank_transactions.csv.gz"), ["amount", "orig_amount"])
    data = Data(
        phase=phase, phase_dir=d, masters_dir=m, txns=txns, items=_items(d, extra),
        customers=read(m / "customers.csv"), suppliers=read(m / "suppliers.csv"),
        categories=list(read(m / "categories.csv").category),
        payment_terms=_num(read(m / "payment_terms.csv"), ["days", "early_payment_discount_pct", "discount_days"]),
        fx=_num(read(m / "fx_rates.csv"), ["units_per_eur"]),
        bank_accounts=read(m / "bank_accounts.csv"), entities=read(m / "entities.csv"),
    )
    start = pd.Timestamp(txns.booking_date.min())
    min_ym = (start - pd.DateOffset(months=LOOKBACK_MONTHS)).strftime("%Y%m")
    before = [find_phase_dir(root, p) for p in {"sample": ["train"], "train": [], "live": ["train"],
                                                 "final": ["live"]}[phase]]

    def batch(name):
        return _concat_dirs([d / name] + [b / name for b in before], min_ym)

    data.dd = _num(batch("direct_debit_batches"), ["batch_total", "amount"])
    data.cards = _num(batch("card_settlements"), ["gross", "fee", "net"])
    data.cheques = _num(batch("cheque_deposits"), ["deposit_total", "amount"])
    data.payruns = _num(batch("payment_runs"), ["transfer_amount", "document_amount"])
    for name, attr in (("remittance_advices", "advices_index"), ("pdfs", "pdf_index")):
        idx = d / name / "index.csv"
        if idx.exists():
            setattr(data, attr, read(idx))
    if phase == "sample":
        _sample_opening_balances(data, find_phase_dir(root, "train"), start.strftime("%Y-%m-%d"))
    return data


def _batch_window(d: Path, lo_ym: str, hi_ym: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(d / "**" / "*.csv"), recursive=True)):
        m = _YM.search(os.path.basename(f))
        if m and lo_ym <= m.group(1) <= hi_ym and not os.path.basename(f).startswith("._"):
            frames.append(read(f).assign(source_file=os.path.basename(f)))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_train_month(root: Path | str, ym: str) -> tuple[Data, Path]:
    """Dev evaluation that mimics LIVE on one TRAIN month (ym = 'YYYY-MM'): that month's bank lines, the FULL open-item
    book at month start rebuilt from TRAIN's golden (like open_items_at_start), items settled in the previous 6 months
    kept with open 0, batch files of the month and the 4 before. Returns (data, path of the month's golden)."""
    root = Path(root)
    d, m = find_phase_dir(root, "train"), find_masters(root, "train")
    start = pd.Timestamp(f"{ym}-01")
    s, e = start.strftime("%Y-%m-%d"), (start + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    cache = root / "cache" / f"month_{ym}"
    cache.mkdir(parents=True, exist_ok=True)
    tx_all = read(d / "bank_transactions.csv.gz")
    txns = _num(tx_all[(tx_all.booking_date >= s) & (tx_all.booking_date <= e)].reset_index(drop=True),
                ["amount", "orig_amount"])
    gpath, ipath = cache / "golden.csv.gz", cache / "items.csv.gz"
    if not (gpath.exists() and ipath.exists()):
        g = read(d / "golden_allocations.csv.gz")
        g[g.bank_txn_id.isin(set(txns.bank_txn_id))].to_csv(gpath, index=False)
        before = set(tx_all.bank_txn_id[tx_all.booking_date < s])
        items = _items(d, [])
        upto = (pd.Timestamp(e) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        items = items[items.issue_date <= upto].reset_index(drop=True)
        g = g[(g.item_id != "") & g.bank_txn_id.isin(before)]
        g = g.assign(amount=pd.to_numeric(g.amount)).sort_values("bank_txn_id", kind="stable")
        tot = dict(zip(items.item_id, items.total))
        open_ = {}
        for item, amt, exc in zip(g.item_id, g.amount, g.exception):
            if item not in tot:
                continue
            cur = open_.get(item, tot[item])
            open_[item] = cur + amt if exc == "RETURNED" else 0.0 if exc in FULL_CLEAR else max(0.0, cur - amt)
        items["open"] = [open_.get(i, t) for i, t in zip(items.item_id, items.total)]
        floor = (start - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
        items = items[(items.open > 0.005) | (items.issue_date >= floor)]
        items.to_csv(ipath, index=False)
    items = read(ipath).reindex(columns=ITEM_COLS).fillna("")
    items = _num(items, ["total", "open"]).reset_index(drop=True)
    data = Data(
        phase="train", phase_dir=d, masters_dir=m, txns=txns, items=items,
        customers=read(m / "customers.csv"), suppliers=read(m / "suppliers.csv"),
        categories=list(read(m / "categories.csv").category),
        payment_terms=_num(read(m / "payment_terms.csv"), ["days", "early_payment_discount_pct", "discount_days"]),
        fx=_num(read(m / "fx_rates.csv"), ["units_per_eur"]),
        bank_accounts=read(m / "bank_accounts.csv"), entities=read(m / "entities.csv"),
    )
    lo = (start - pd.DateOffset(months=LOOKBACK_MONTHS)).strftime("%Y%m")
    hi = start.strftime("%Y%m")
    data.dd = _num(_batch_window(d / "direct_debit_batches", lo, hi), ["batch_total", "amount"])
    data.cards = _num(_batch_window(d / "card_settlements", lo, hi), ["gross", "fee", "net"])
    data.cheques = _num(_batch_window(d / "cheque_deposits", lo, hi), ["deposit_total", "amount"])
    data.payruns = _num(_batch_window(d / "payment_runs", lo, hi), ["transfer_amount", "document_amount"])
    return data, gpath


FULL_CLEAR = {"", "FX_DIFFERENCE", "SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES", "OVERPAYMENT"}


def _sample_opening_balances(data: Data, train_dir: Path, start: str, cache: Path | None = None) -> None:
    """The sample month has no open_items_at_start; rebuild it from TRAIN's golden before the month (dev only),
    the way the organisers' open_items_at_start is defined, so sample scores behave like LIVE."""
    cache = cache or train_dir.parent.parent / "cache" / f"sample_open_{start}.csv"
    if cache.exists():
        op = read(cache)
    else:
        tx = read(train_dir / "bank_transactions.csv.gz")[["bank_txn_id", "booking_date"]]
        before = set(tx.bank_txn_id[tx.booking_date < start])
        g = read(train_dir / "golden_allocations.csv.gz")
        g = g[(g.item_id != "") & g.bank_txn_id.isin(before) & g.item_id.isin(set(data.items.item_id))]
        g = g.assign(amount=pd.to_numeric(g.amount), seq=g.bank_txn_id)
        tot = dict(zip(data.items.item_id, data.items.total))
        open_ = {}
        for r in g.sort_values("seq").itertuples():
            cur = open_.get(r.item_id, tot[r.item_id])
            if r.exception == "RETURNED":
                cur += r.amount
            elif r.exception in FULL_CLEAR:
                cur = 0.0
            else:
                cur = max(0.0, cur - r.amount)
            open_[r.item_id] = cur
        op = pd.DataFrame({"item_id": list(open_), "open": [f"{v:.2f}" for v in open_.values()]})
        cache.parent.mkdir(parents=True, exist_ok=True)
        op.to_csv(cache, index=False)
    m = dict(zip(op.item_id, pd.to_numeric(op.open)))
    data.items["open"] = [m.get(i, o) for i, o in zip(data.items.item_id, data.items.open)]
