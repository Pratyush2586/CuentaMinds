"""Synthetic month-end close dataset: messy raw files plus an answer key.

`generate(out_dir, seed, n_invoices)` writes a three-entity company (ES01, DE01, FR01 in
EUR; DE01 also runs a USD bank account) for period 2026-10. The clean base data is built
so that no check fires on it. Traps are then planted, each meant to raise exactly one
finding, and listed in answer_key.json as {"category", "check", "record_id"} where
record_id is the finding's record_ids[0] (entity pairs and shared-IBAN vendors use the
sorted ids joined by "|"). Decoys that look suspicious but are legitimate (credit notes,
one invoice number used by two vendors, an IBAN change 60 days before payment, a 1.5 %
PO price variance) are planted too and are not in the key.

All randomness comes from random.Random(seed), so a seed always yields identical files.
"""
from __future__ import annotations

import csv
import io
import json
import random
import re
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from rapidfuzz import fuzz

from closeops.ingest.normalize import norm_invoice_no, norm_vendor

MIN_INVOICES = 200
MIN_BASE_VENDORS = 90  # enough untouched vendors for the vendor-level traps at small n
ENTITIES = {"ES01": "ES", "DE01": "DE", "FR01": "FR"}
USD_ENTITY = "DE01"
PERIOD_START, PERIOD_END = date(2026, 10, 1), date(2026, 10, 31)
FIRST_INVOICE, LAST_INVOICE = date(2026, 8, 3), date(2026, 10, 30)
LAST_BANK_DAY = date(2026, 10, 30)
# Mirror config/policy.yaml (fraud.approval_limit, fraud.split_window_days) so that the
# clean data never forms a split-invoice cluster by accident.
APPROVAL_LIMIT, SPLIT_WINDOW = 10000.0, 7
FIXED_ZIP_TIME = (2026, 11, 1, 0, 0, 0)

TRAP_COUNTS = {
    "dup_exact": 12, "dup_near": 6, "dup_cross_entity": 2, "dup_payment": 3,
    "tax_math": 4, "tax_amount": 4, "tax_rate": 4, "tax_reverse_charge": 4,
    "iban_changed_before_payment": 4, "iban_not_in_master": 2, "split_invoices": 2,
    "shared_iban": 1, "po_price_mismatch": 10, "contract_price_mismatch": 5,
    "qty_over_received": 5, "no_po": 6, "bank_unmatched": 8,
    "intercompany_mismatch": 2, "cutoff": 6,
}
CATEGORY = {
    "dup_exact": "duplicate", "dup_near": "duplicate", "dup_cross_entity": "duplicate",
    "dup_payment": "duplicate", "tax_math": "tax", "tax_amount": "tax", "tax_rate": "tax",
    "tax_reverse_charge": "tax", "iban_changed_before_payment": "fraud",
    "iban_not_in_master": "fraud", "split_invoices": "fraud", "shared_iban": "fraud",
    "po_price_mismatch": "match", "contract_price_mismatch": "match",
    "qty_over_received": "match", "no_po": "match", "bank_unmatched": "bankrec",
    "intercompany_mismatch": "gl", "cutoff": "gl",
}

STANDARD_VAT = {"ES": 21.0, "DE": 19.0, "FR": 20.0, "NL": 21.0, "BE": 21.0, "IT": 22.0,
                "AT": 20.0, "PT": 23.0, "IE": 23.0}
REDUCED_VAT = {"ES": [10.0, 4.0], "DE": [7.0], "FR": [10.0, 5.5]}
WRONG_VAT = {"ES": [16.0, 18.0], "DE": [16.0], "FR": [19.6]}  # outdated / non-existent rates
TAX_RATE_ROWS = [("ES", "21%", "standard"), ("ES", "10%", "reduced"), ("ES", "4%", "super-reduced"),
                 ("ES", "0%", "zero"), ("DE", "19%", "standard"), ("DE", "7%", "reduced"),
                 ("DE", "0%", "exempt"), ("FR", "20%", "standard"), ("FR", "10%", "reduced"),
                 ("FR", "5,5%", "reduced"), ("FR", "0%", "exempt")]
INTRA_COUNTRIES = ["NL", "BE", "IT", "AT", "PT", "IE", "ES", "DE", "FR"]
GROUP_SUPPLIERS = [("NL", "intra"), ("BE", "intra"), ("IT", "intra"), ("CH", "non_eu"),
                   ("AT", "intra"), ("NL", "intra")]
IBAN_LEN = {"ES": 24, "DE": 22, "FR": 27, "NL": 18, "BE": 16, "IT": 27, "AT": 20, "PT": 25,
            "IE": 22, "GB": 22, "CH": 21, "US": 20}

NAME_PARTS = {
    "ES": (["Suministros", "Transportes", "Talleres", "Distribuciones", "Servicios", "Industrias"],
           ["Iberia", "del Norte", "Levante", "Cantábrico", "Meseta", "Mediterráneo", "Castilla"],
           ["S.L.", "S.A."]),
    "DE": (["Rhein", "Alpen", "Nord", "Süd", "Hansa", "Elbe", "Weser"],
           ["Logistik", "Technik", "Stahlbau", "Verpackung", "Elektro", "Datentechnik"],
           ["GmbH", "AG", "KG"]),
    "FR": (["Société", "Groupe", "Ateliers", "Établissements", "Compagnie"],
           ["Garonne", "Loire", "Lumière", "Provence", "Normandie", "Rhône", "Bretagne"],
           ["SAS", "SARL", "SA"]),
    "NL": (["Holland", "Delta", "Polder", "Oranje"], ["Logistics", "Packaging", "Systems"], ["B.V."]),
    "BE": (["Flanders", "Brabant", "Ardennes"], ["Industries", "Services", "Supply"], ["NV", "BV"]),
    "IT": (["Officine", "Gruppo", "Fratelli"], ["Lombarde", "Adriatica", "Toscana"], ["S.r.l.", "S.p.A."]),
    "AT": (["Donau", "Tirol", "Wiener"], ["Maschinen", "Handel", "Systeme"], ["GmbH"]),
    "PT": (["Lusitania", "Atlântico", "Douro"], ["Componentes", "Serviços", "Têxteis"], ["Lda"]),
    "IE": (["Shannon", "Liffey", "Emerald"], ["Software", "Analytics", "Labs"], ["Ltd"]),
    "GB": (["Thames", "Pennine", "Albion", "Mersey"], ["Instruments", "Software", "Components"],
           ["Ltd", "PLC"]),
    "CH": (["Helvetia", "Matterhorn", "Jura"], ["Precision", "Robotics", "Consulting"], ["AG", "SA"]),
}
NAME_EXTRA = ["Industrial", "Trade", "Partners", "Solutions", "Supply", "Works", "Center", "Plus"]
CUSTOMERS = {
    "ES": ["Hoteles Costa Brava", "Mercado Central", "Construcciones Ribera", "Bodegas Altamira",
           "Clínica San Rafael", "Autocares Pérez"],
    "DE": ["Bäckerei Maier", "Stadtwerke Lindau", "Autohaus König", "Klinikum Nordheide",
           "Brauerei Feldmann", "Möbel Kranz"],
    "FR": ["Hôtel du Parc", "Boulangerie Martin", "Clinique Pasteur", "Transports Lefèvre",
           "Mairie de Vienne", "Garage Moreau"],
}
USD_CUSTOMERS = ["Harbor Freight Partners", "Lakeside Medical Inc", "Summit Retail LLC"]
APPROVERS = ["m.garcia", "j.schmidt", "c.dubois", "a.lopez", "k.weber", "s.martin"]
REF_TEMPLATES = {"ES": ["Pago fra. {no}", "FRA {no}", "{no}"],
                 "DE": ["Rechnung {no}", "RE {no}", "{no}"],
                 "FR": ["Facture {no}", "FACT {no}", "{no}"]}
BATCH_REFS = {"ES": "Pago agrupado", "DE": "Sammelzahlung", "FR": "Paiement groupé"}
FEE_NAMES = {"ES": "Comisiones bancarias", "DE": "Kontoführungsgebühr", "FR": "Frais bancaires"}
ITEMS = [  # item, expense account, unit price range, VAT-exempt when domestic
    ("Steel brackets", "6000", (1.2, 25.0), False), ("Packaging film", "6000", (3.5, 40.0), False),
    ("Euro pallets", "6000", (8.0, 18.0), False), ("Cable assemblies", "6000", (4.0, 95.0), False),
    ("Fasteners M8", "6000", (1.2, 6.5), False), ("Hydraulic oil", "6000", (4.0, 12.0), False),
    ("Consulting hours", "6100", (65.0, 180.0), False), ("Cleaning service", "6100", (180.0, 2400.0), False),
    ("Maintenance visit", "6100", (240.0, 1900.0), False), ("Security service", "6100", (900.0, 4800.0), False),
    ("Freight FTL", "6200", (450.0, 2400.0), False), ("Courier parcels", "6200", (6.0, 48.0), False),
    ("Warehouse storage", "6200", (9.0, 35.0), False), ("Software licence", "6300", (25.0, 900.0), False),
    ("Cloud hosting", "6300", (300.0, 6000.0), False), ("IT support hours", "6300", (55.0, 140.0), False),
    ("Insurance premium", "6400", (400.0, 9000.0), True), ("Training course", "6400", (250.0, 3200.0), True),
]
NET_BANDS = [(150.0, 1500.0)] * 5 + [(1500.0, 6000.0)] * 4 + [(6000.0, 24000.0)]
SERVICE_ACCOUNTS = ("6100", "6300", "6400")
ACCOUNTS = [("1000", "Bank EUR", "bank"), ("1010", "Bank USD", "bank"),
            ("1200", "Trade receivables", "asset"), ("1300", "Input VAT", "asset"),
            ("1400", "Prepaid expenses", "asset"),
            ("1500", "Intercompany receivables", "intercompany receivable"),
            ("2000", "Trade payables", "liability"), ("2100", "Accrued expenses", "liability"),
            ("2500", "Intercompany payables", "intercompany payable"), ("4000", "Revenue", "revenue"),
            ("6000", "Materials", "expense"), ("6100", "External services", "expense"),
            ("6200", "Freight and logistics", "expense"), ("6300", "IT and software", "expense"),
            ("6400", "Insurance and training", "expense"), ("6800", "Bank charges", "expense")]


@dataclass
class CatItem:
    item: str
    account: str
    price: float
    exempt: bool


@dataclass(eq=False)
class Vendor:
    id: str
    name: str
    country: str
    vat_id: str
    iban: str
    entities: list[str]
    currency: str
    terms: int
    kind: str  # domestic | intra | non_eu
    catalog: list[CatItem]
    role: str  # base | split | ghost
    style: int
    prefix: str
    next_no: int
    spaced_iban: bool


@dataclass(eq=False)
class Inv:
    seq: int
    entity: str
    vendor: Vendor
    no: str
    date: date
    spe: date | None
    item: str
    account: str
    qty: int
    price: float
    currency: str
    net: float
    rate: float
    tax: float
    gross: float
    has_gr: bool
    role: str = "base"  # base | copy | split | accrual | credit
    status: str = "open"
    pay_date: date | None = None
    po_mode: str = "normal"  # normal | blank | bogus
    po_price: float | None = None
    gr_short: int = 0
    source: Inv | None = None  # invoice a copy / credit note derives from (shares its PO)
    tags: set = field(default_factory=set)
    id: str = ""
    po_id: str = ""


@dataclass(eq=False)
class Line:
    seq: int
    entity: str
    date: date
    currency: str
    name: str
    iban: str | None
    ref_fmt: str
    kind: str  # payment | refund | receipt | fee | advance | unknown
    invs: list[Inv] = field(default_factory=list)
    fixed: float | None = None  # signed amount when not derived from invoices
    fx_rate: float | None = None  # EUR payment of a foreign-currency invoice
    copy_of: Line | None = None
    gl: bool = True  # mirrored by a bank-account GL posting
    tags: set = field(default_factory=set)
    id: str = ""

    def amount(self) -> float:
        if self.copy_of is not None:
            return self.copy_of.amount()
        if self.fixed is not None:
            return self.fixed
        total = sum(i.gross for i in self.invs)
        if self.kind == "refund":
            return round(-total, 2)
        return -round(total * self.fx_rate if self.fx_rate else total, 2)

    def ref(self) -> str:
        if self.copy_of is not None:
            return self.copy_of.ref()
        nos = [i.no for i in self.invs] + ["", ""]
        return self.ref_fmt.format(no=nos[0], no2=nos[1])


# -- small helpers -----------------------------------------------------------------
def _weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _cents(x: float) -> int:
    return int(round(abs(x) * 100))


def _eu(x: float) -> str:
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _dmy(d: date, sep: str) -> str:
    return f"{d.day:02d}{sep}{d.month:02d}{sep}{d.year}"


def _spaced(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def _respell(no: str) -> str:
    """Another spelling of an alpha-prefixed invoice number with the same normal form."""
    m = re.fullmatch(r"([A-Za-z]+)[-/ ]?(\d+)", no)
    if not m:
        return no
    prefix, digits = m.groups()
    for alt in (f"{prefix.lower()} {int(digits)}", f"{prefix}{digits}", f"{prefix}-{int(digits)}"):
        if alt != no and norm_invoice_no(alt) == norm_invoice_no(no):
            return alt
    return no


def _csv(rows: list[list], delimiter: str = ",") -> str:
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _stable_xlsx(data: bytes) -> bytes:
    """Rewrite an xlsx zip with fixed timestamps so equal content gives equal bytes."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*",
                              rb"\g<1>2026-11-01T00:00:00Z", body)
            zi = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(zi, body)
    return out.getvalue()


class _Builder:
    def __init__(self, seed: int, n_invoices: int):
        self.rng = random.Random(seed)
        self.n = n_invoices
        self.vendors: list[Vendor] = []
        self.invoices: list[Inv] = []
        self.lines: list[Line] = []
        self.contracts: list[dict] = []
        self.contract_items: set[tuple[str, str]] = set()
        self.history: list[tuple[str, str, date, str]] = []  # vendor, iban, changed_at, by
        self.gl: list[list] = []
        self.key: list[tuple[str, object]] = []  # (check, Inv | Line | str)
        self.amounts: set[int] = set()  # cents of every invoice gross / bank amount in use
        self.ibans: set[str] = set()
        self.names: set[str] = set()
        self.reserved: set[str] = set()  # vendors owned by a payment-level trap
        self.fx: list[tuple[date, float]] = []
        self._n_seq = 0
        self._n_je = 0

    # -- primitives ----------------------------------------------------------------
    def _seq(self) -> int:
        self._n_seq += 1
        return self._n_seq

    def _amount_free(self, x: float) -> bool:
        c = _cents(x)
        if abs(x) >= 5000 and c % 100000 == 0:  # keep clear of the round-amount soft signal
            return False
        return c - 1 not in self.amounts and c not in self.amounts and c + 1 not in self.amounts

    def _use(self, x: float) -> None:
        self.amounts.add(_cents(x))

    def _unique_amount(self, lo: float, hi: float) -> float:
        while True:
            x = round(self.rng.uniform(lo, hi), 2)
            if self._amount_free(x):
                self._use(x)
                return x

    def _new_iban(self, country: str) -> str:
        while True:
            bban = "".join(str(self.rng.randrange(10)) for _ in range(IBAN_LEN[country] - 4))
            num = "".join(str(int(ch, 36)) for ch in bban + country + "00")
            iban = f"{country}{98 - int(num) % 97:02d}{bban}"
            if iban not in self.ibans:
                self.ibans.add(iban)
                return iban

    def _name(self, country: str) -> str:
        first, second, suffixes = NAME_PARTS[country]
        for _ in range(1000):
            words = [self.rng.choice(first), self.rng.choice(second)]
            if self.rng.random() < 0.4:
                words.append(self.rng.choice(NAME_EXTRA))
            name = " ".join(words + [self.rng.choice(suffixes)])
            if norm_vendor(name) not in self.names:
                self.names.add(norm_vendor(name))
                return name
        raise RuntimeError(f"ran out of vendor names for {country}")

    def _vat_id(self, country: str) -> str:
        digits = "".join(str(self.rng.randrange(10)) for _ in range(9))
        return {"ES": f"ESB{digits[:8]}", "CH": f"CHE{digits}", "FR": f"FR{digits}{digits[:2]}"}.get(
            country, f"{country}{digits}")

    def _fx_on(self, d: date) -> float:
        return [r for day, r in self.fx if day <= d][-1]

    # -- vendors and invoices ------------------------------------------------------
    def _vendor(self, country: str, entities: list[str], kind: str, currency: str = "EUR",
                role: str = "base") -> Vendor:
        rng = self.rng
        name = self._name(country)
        catalog = [CatItem(item, acct, round(rng.uniform(lo, hi), 2), exempt)
                   for item, acct, (lo, hi), exempt in rng.sample(ITEMS, rng.randint(2, 3))]
        abbr = re.sub(r"[^A-Z]", "", name.upper())[:3] or "VEN"
        v = Vendor(id=f"V{10001 + len(self.vendors)}", name=name, country=country,
                   vat_id=self._vat_id(country), iban=self._new_iban(country),
                   entities=list(entities), currency=currency,
                   terms=rng.choice([14, 30, 30, 45, 60]), kind=kind, catalog=catalog, role=role,
                   style=rng.randrange(4), prefix=rng.choice(["INV", "FAC", "RE", "F", abbr]),
                   next_no=rng.randint(100, 9000), spaced_iban=rng.random() < 0.5)
        self.vendors.append(v)
        if role == "base" and rng.random() < 0.35:
            ci = catalog[0]
            self.contract_items.add((v.id, ci.item))
            self._contract(v, ci.item, ci.price, date(2026, 1, 1), date(2026, 12, 31))
            if rng.random() < 0.5:  # last year's contract, expired, at an older price
                self._contract(v, ci.item, round(ci.price * 0.95, 2), date(2025, 1, 1),
                               date(2025, 12, 31))
        return v

    def _contract(self, v: Vendor, item: str, price: float, start: date, end: date) -> None:
        self.contracts.append({"contractRef": f"CT-{len(self.contracts) + 1:04d}", "supplier": v.id,
                               "material": item, "agreedPrice": price, "currency": v.currency,
                               "validFrom": start.isoformat(), "validTo": end.isoformat(),
                               "paymentTermsDays": v.terms})

    def _dates(self) -> list[date]:
        d, out = FIRST_INVOICE + timedelta(days=self.rng.randint(0, 15)), []
        while True:
            d = _weekday(d)
            if d > LAST_INVOICE:
                return out
            out.append(d)
            d += timedelta(days=self.rng.randint(16, 24))

    def _next_no(self, v: Vendor) -> str:
        v.next_no += self.rng.randint(1, 40)
        n = v.next_no
        no = [f"{v.prefix}-{n:05d}", f"2026/{n:04d}", f"{v.prefix}{n}", str(100000 + n)][v.style]
        if v.style in (0, 2) and self.rng.random() < 0.1:
            no = _respell(no)
        return no

    def _rate(self, v: Vendor, buyer: str, exempt: bool) -> float:
        if v.country != buyer or exempt:
            return 0.0
        if self.rng.random() < 0.12:
            return self.rng.choice(REDUCED_VAT[buyer])
        return STANDARD_VAT[buyer]

    def _new_invoice(self, v: Vendor, entity: str, d: date, lo: float = 100.0,
                     hi: float | None = None, spe: date | None = None, role: str = "base") -> Inv:
        rng = self.rng
        buyer = ENTITIES[entity]
        for attempt in range(600):
            ci = rng.choice(v.catalog)
            contract = (v.id, ci.item) in self.contract_items
            price = ci.price if contract else round(ci.price * rng.uniform(0.97, 1.03), 2)
            rate = self._rate(v, buyer, ci.exempt)
            band = (lo, hi) if hi is not None else rng.choice(NET_BANDS)
            target = rng.uniform(*band) / (1 + rate / 100)
            qty = 1 if ci.exempt else max(1, round(target / price)) + attempt // 100
            net = round(qty * price, 2)
            tax = round(net * rate / 100, 2)
            gross = round(net + tax, 2)
            if gross < lo or (hi is not None and gross > hi) or not self._amount_free(gross):
                continue
            self._use(gross)
            if spe is None and rng.random() > 0.08:
                spe = d - timedelta(days=0 if ci.account in ("6000", "6200") else rng.randint(0, 12))
            return Inv(seq=self._seq(), entity=entity, vendor=v, no=self._next_no(v), date=d,
                       spe=spe, item=ci.item, account=ci.account, qty=qty, price=price,
                       currency=v.currency, net=net, rate=rate, tax=tax, gross=gross,
                       has_gr=ci.account in ("6000", "6200") or rng.random() < 0.6, role=role)
        raise RuntimeError(f"could not draw a unique invoice amount for {v.id}")

    def _vendors_and_invoices(self) -> None:
        rng = self.rng
        ents = list(ENTITIES)
        special = [self._vendor(c, ents, kind) for c, kind in GROUP_SUPPLIERS]
        special += [self._vendor("GB", [USD_ENTITY], "non_eu", currency="USD") for _ in range(3)]
        self.split_vendors = [self._vendor(ENTITIES[e], [e], "domestic", role="split")
                              for e in rng.sample(ents, 2)]
        slots = [(v, d) for v in special for d in self._dates()]
        i = 0
        while len(slots) < self.n or i < MIN_BASE_VENDORS:
            e = ents[i % 3]
            i += 1
            r = rng.random()
            if r < 0.74:
                v = self._vendor(ENTITIES[e], [e], "domestic")
            elif r < 0.94:
                c = rng.choice([c for c in INTRA_COUNTRIES if c != ENTITIES[e]])
                v = self._vendor(c, [e], "intra")
            else:
                v = self._vendor(rng.choice(["CH", "GB"]), [e], "non_eu")
            slots += [(v, d) for d in self._dates()]
        drop = set(rng.sample(range(len(slots)), len(slots) - self.n))
        for k, (v, d) in enumerate(slots):
            if k not in drop:
                self.invoices.append(self._new_invoice(v, rng.choice(v.entities), d))

    def _payments(self) -> None:
        rng = self.rng
        for inv in self.invoices:
            due = _weekday(inv.date + timedelta(days=inv.vendor.terms + rng.randint(-3, 4)))
            if due < PERIOD_START:
                inv.status = "paid"  # settled before the period: not on the October statement
            elif due <= LAST_BANK_DAY and rng.random() > 0.06:
                inv.status, inv.pay_date = "paid", due
        for inv in self.invoices:
            if inv.pay_date is not None:
                self.lines.append(self._pay_line(inv, inv.pay_date))

    def _bank_name(self, v: Vendor) -> str:
        return self.rng.choice([v.name, v.name.upper(), v.name.upper()[:22]])

    def _pay_line(self, inv: Inv, d: date) -> Line:
        country = ENTITIES[inv.entity]
        fmt = "" if self.rng.random() < 0.12 else self.rng.choice(REF_TEMPLATES[country])
        return Line(seq=self._seq(), entity=inv.entity, date=d, currency=inv.currency,
                    name=self._bank_name(inv.vendor), iban=inv.vendor.iban, ref_fmt=fmt,
                    kind="payment", invs=[inv])

    def _plain_vendor(self, v: Vendor) -> bool:
        return (v.role == "base" and v.kind == "domestic" and len(v.entities) == 1
                and v.currency == "EUR" and v.id not in self.reserved)

    def _plain_line(self, ln: Line) -> bool:
        return (ln.kind == "payment" and len(ln.invs) == 1 and not ln.tags
                and not ln.invs[0].tags and ln.invs[0].role == "base"
                and self._plain_vendor(ln.invs[0].vendor))

    def _vendor_lines(self, v: Vendor) -> list[Line]:
        return sorted((ln for ln in self.lines if ln.invs and ln.invs[0].vendor is v),
                      key=lambda ln: (ln.date, ln.seq))

    def _batch_and_fx_lines(self) -> None:
        """Clean variety: batch payments (one line, two invoices) and USD invoices paid in EUR."""
        rng = self.rng
        cands = [v for v in self.vendors if self._plain_vendor(v)
                 and sum(self._plain_line(ln) for ln in self._vendor_lines(v)) >= 2]
        for v in rng.sample(cands, min(4, len(cands))):
            a, b = [ln for ln in self._vendor_lines(v) if self._plain_line(ln)][-2:]
            total = a.invs[0].gross + b.invs[0].gross
            if not self._amount_free(total):
                continue
            self._use(total)
            b.invs = [a.invs[0], b.invs[0]]
            b.ref_fmt = rng.choice(["{no} {no2}", "{no}, {no2}", BATCH_REFS[ENTITIES[b.entity]]])
            b.tags.add("batch")
            for i in b.invs:
                i.tags.add("batch")
            self.lines.remove(a)
            self.reserved.add(v.id)
        usd = [ln for ln in self.lines if ln.currency == "USD" and len(ln.invs) == 1]
        for ln in rng.sample(usd, min(2, len(usd))):
            rate = self._fx_on(ln.date)
            if not self._amount_free(ln.invs[0].gross * rate):
                continue
            ln.currency, ln.fx_rate = "EUR", rate
            self._use(ln.amount())
            ln.tags.add("fx")
            ln.invs[0].tags.add("fx")

    # -- invoice-level traps -------------------------------------------------------
    def _candidates(self, pred) -> list[Inv]:
        return [i for i in self.invoices if i.role == "base" and not i.tags and pred(i)]

    def _take(self, tag: str, pred) -> Inv:
        cands = self._candidates(pred)
        if not cands:
            raise RuntimeError(f"no candidate invoice for {tag}")
        inv = self.rng.choice(cands)
        inv.tags.add(tag)
        return inv

    def _split_safe(self, new: Inv) -> bool:
        """True if adding `new` keeps its vendor clear of a split-invoice cluster."""
        pts = sorted((i.date, i.gross) for i in self.invoices + [new]
                     if i.vendor is new.vendor and i.net > 0 and i.gross < APPROVAL_LIMIT)
        run_len, total, prev = 0, 0.0, None
        for d, g in pts:
            if prev is not None and (d - prev).days > SPLIT_WINDOW:
                if run_len >= 2 and total >= APPROVAL_LIMIT:
                    return False
                run_len, total = 0, 0.0
            run_len, total, prev = run_len + 1, total + g, d
        return not (run_len >= 2 and total >= APPROVAL_LIMIT)

    def _clone(self, src: Inv, **changes) -> Inv:
        return replace(src, seq=self._seq(), role="copy", status="open", pay_date=None,
                       source=src, tags=set(), **changes)

    def _plant_copy(self, check: str, pred, make) -> None:
        """Add a duplicate of a clean invoice; `make(orig)` returns the copy or None."""
        cands = self._candidates(pred)
        self.rng.shuffle(cands)
        for orig in cands:
            copy = make(orig)
            if copy is not None and copy.date <= LAST_INVOICE and self._split_safe(copy):
                orig.tags.add("dup_source")
                self.invoices.append(copy)
                self.key.append((check, copy))
                return
        raise RuntimeError(f"no candidate invoice for {check}")

    def _near_no(self, orig: Inv) -> str | None:
        taken = {norm_invoice_no(i.no) for i in self.invoices if i.vendor is orig.vendor}
        variants = [orig.no + "A", orig.no + "-B", orig.no + "/1", orig.no + "R"]
        self.rng.shuffle(variants)
        base = norm_invoice_no(orig.no)
        for v in variants:
            n = norm_invoice_no(v)
            if n not in taken and fuzz.ratio(n, base) >= 90:
                return v
        return None

    def _plant_tax(self, check: str, pred, make) -> None:
        """Re-price a clean invoice; `make(inv)` returns (rate, tax, gross), the gross unique."""
        cands = self._candidates(pred)
        self.rng.shuffle(cands)
        for inv in cands:
            self.amounts.discard(_cents(inv.gross))
            for _ in range(20):
                rate, tax, gross = make(inv)
                if self._amount_free(gross):
                    inv.rate, inv.tax, inv.gross = rate, tax, gross
                    self._use(gross)
                    inv.tags.add(check)
                    self.key.append((check, inv))
                    return
            self._use(inv.gross)
        raise RuntimeError(f"no candidate invoice for {check}")

    def _invoice_traps(self) -> None:
        rng = self.rng

        def domestic(i: Inv) -> bool:
            return i.vendor.kind == "domestic" and i.currency == "EUR" and i.vendor.role == "base"

        def standard(i: Inv) -> bool:
            return domestic(i) and i.rate == STANDARD_VAT[ENTITIES[i.entity]]

        def no_contract(i: Inv) -> bool:
            return (i.vendor.id, i.item) not in self.contract_items

        def dup_source(i: Inv) -> bool:
            return i.vendor.role == "base" and i.gross < 4500 and i.date <= date(2026, 10, 26)

        # Decoy: the same invoice number used by two different vendors in one entity.
        for _ in range(3):
            a = self._take("decoy_same_no", lambda i: i.vendor.role == "base")
            norm = norm_invoice_no(a.no)
            b = self._take("decoy_same_no", lambda i: i.entity == a.entity and i.vendor is not a.vendor
                           and all(norm_invoice_no(x.no) != norm for x in self.invoices
                                   if x.vendor is i.vendor))
            b.no = a.no
        # Decoy: PO price 1.5 % below the invoice price (inside the 2 % tolerance).
        for _ in range(3):
            inv = self._take("decoy_variance", lambda i: no_contract(i) and i.price >= 20)
            inv.po_price = round(inv.price / 1.015, 2)

        for _ in range(TRAP_COUNTS["dup_exact"]):
            self._plant_copy("dup_exact", dup_source, lambda o: self._clone(
                o, date=_weekday(o.date + timedelta(days=rng.randint(0, 3))),
                no=_respell(o.no) if rng.random() < 0.5 else o.no))

        def near(o: Inv) -> Inv | None:
            no = self._near_no(o)
            d = _weekday(o.date + timedelta(days=rng.randint(1, 4)))
            return None if no is None or (d - o.date).days > 7 else self._clone(o, no=no, date=d)

        for _ in range(TRAP_COUNTS["dup_near"]):
            self._plant_copy("dup_near", dup_source, near)
        for _ in range(TRAP_COUNTS["dup_cross_entity"]):
            self._plant_copy("dup_cross_entity", lambda i: dup_source(i) and len(i.vendor.entities) > 1,
                             lambda o: self._clone(
                                 o, entity=rng.choice([e for e in o.vendor.entities if e != o.entity]),
                                 date=_weekday(o.date + timedelta(days=rng.randint(0, 5)))))

        def delta(lo: float, hi: float) -> float:
            return rng.choice([-1, 1]) * round(rng.uniform(lo, hi), 2)

        def wrong_total(i: Inv):
            return i.rate, i.tax, round(i.net + i.tax + delta(1, 40), 2)

        def wrong_tax(i: Inv):
            tax = round(i.tax + delta(0.5, 25), 2)
            tax = tax if tax > 0 else round(i.tax + rng.uniform(0.5, 25), 2)
            return i.rate, tax, round(i.net + tax, 2)

        def charged(rate: float, i: Inv):
            tax = round(i.net * rate / 100, 2)
            return rate, tax, round(i.net + tax, 2)

        for _ in range(TRAP_COUNTS["tax_math"]):
            self._plant_tax("tax_math", standard, wrong_total)
        for _ in range(TRAP_COUNTS["tax_amount"]):
            self._plant_tax("tax_amount", standard, wrong_tax)
        for _ in range(TRAP_COUNTS["tax_rate"]):  # a rate that is not valid in the buyer country
            self._plant_tax("tax_rate", domestic,
                            lambda i: charged(rng.choice(WRONG_VAT[ENTITIES[i.entity]]), i))
        for _ in range(TRAP_COUNTS["tax_reverse_charge"]):  # intra-EU vendor charged its home VAT
            self._plant_tax("tax_reverse_charge",
                            lambda i: i.vendor.kind == "intra" and len(i.vendor.entities) == 1,
                            lambda i: charged(STANDARD_VAT[i.vendor.country], i))

        for _ in range(TRAP_COUNTS["po_price_mismatch"]):
            inv = self._take("po_price_mismatch", lambda i: no_contract(i) and i.price >= 5)
            inv.po_price = round(inv.price / rng.uniform(1.05, 1.15), 2)
            self.key.append(("po_price_mismatch", inv))
        for k in range(TRAP_COUNTS["contract_price_mismatch"]):
            inv = self._take("contract_price_mismatch", no_contract)
            inv.item = f"{inv.item} - framework {k + 1}"
            self._contract(inv.vendor, inv.item, round(inv.price / rng.uniform(1.06, 1.12), 2),
                           date(2026, 1, 1), date(2026, 12, 31))
            self.key.append(("contract_price_mismatch", inv))
        for _ in range(TRAP_COUNTS["qty_over_received"]):
            inv = self._take("qty_over_received", lambda i: i.has_gr and i.qty >= 3)
            inv.gr_short = rng.randint(1, max(1, inv.qty // 4))
            self.key.append(("qty_over_received", inv))
        for k in range(TRAP_COUNTS["no_po"]):
            inv = self._take("no_po", lambda i: True)
            inv.po_mode = "blank" if k % 2 == 0 else "bogus"
            self.key.append(("no_po", inv))
        for _ in range(3):  # cut-off: invoiced in October for service beyond the period end
            inv = self._take("cutoff", lambda i: i.date >= PERIOD_START and i.account in SERVICE_ACCOUNTS)
            inv.spe = rng.choice([date(2026, 11, 30), date(2026, 12, 31)])
            self.key.append(("cutoff", inv))

    # -- payment-level traps -------------------------------------------------------
    def _take_line(self, tag: str, pred=lambda ln: True) -> Line:
        cands = [ln for ln in self.lines if self._plain_line(ln) and pred(ln)]
        if not cands:
            raise RuntimeError(f"no candidate bank line for {tag}")
        ln = self.rng.choice(cands)
        ln.tags.add(tag)
        self.reserved.add(ln.invs[0].vendor.id)
        return ln

    def _change_candidates(self) -> list[tuple[Vendor, list[Line]]]:
        out = []
        for v in self.vendors:
            if self._plain_vendor(v):
                lines = self._vendor_lines(v)
                if lines and self._plain_line(lines[-1]):
                    out.append((v, lines))
        return out

    def _bank_change(self, v: Vendor, changed_at: date) -> str:
        old, new = v.iban, self._new_iban(v.country)
        created = date(self.rng.randint(2019, 2023), self.rng.randint(1, 12), self.rng.randint(1, 28))
        self.history.append((v.id, old, created, "migration"))
        self.history.append((v.id, new, changed_at, self.rng.choice(APPROVERS)))
        v.iban = new
        return new

    def _payment_traps(self) -> None:
        rng = self.rng
        for _ in range(TRAP_COUNTS["dup_payment"]):
            def offsets(ln: Line) -> list[date]:
                return [ln.date + timedelta(days=k) for k in (1, 2, 3)
                        if (ln.date + timedelta(days=k)).weekday() < 5
                        and ln.date + timedelta(days=k) <= LAST_BANK_DAY]
            src = self._take_line("dup_payment_source", lambda ln: bool(offsets(ln)))
            dup = Line(seq=self._seq(), entity=src.entity, date=rng.choice(offsets(src)),
                       currency=src.currency, name=src.name, iban=src.iban, ref_fmt="",
                       kind="payment", copy_of=src, tags={"dup_payment"})
            self.lines.append(dup)
            self.key.append(("dup_payment", dup))
        for _ in range(TRAP_COUNTS["iban_not_in_master"]):
            ln = self._take_line("iban_not_in_master")
            ln.iban = self._new_iban(ln.invs[0].vendor.country)
            if "{no}" not in ln.ref_fmt:
                ln.ref_fmt = "{no}"
            self.key.append(("iban_not_in_master", ln))

        cands = self._change_candidates()
        rng.shuffle(cands)
        planted = 0
        for v, lines in cands:
            if planted == TRAP_COUNTS["iban_changed_before_payment"]:
                break
            last = lines[-1]
            room = 25 if len(lines) == 1 else min(25, (last.date - lines[-2].date).days - 1)
            if room < 1:
                continue
            last.iban = self._bank_change(v, last.date - timedelta(days=rng.randint(1, room)))
            last.tags.add("iban_changed")
            self.reserved.add(v.id)
            self.key.append(("iban_changed_before_payment", last))
            planted += 1
        if planted < TRAP_COUNTS["iban_changed_before_payment"]:
            raise RuntimeError("not enough vendors for iban_changed_before_payment")

        # Decoy: bank details changed 60 days before the first October payment.
        v, lines = rng.choice(self._change_candidates())
        new = self._bank_change(v, lines[0].date - timedelta(days=60))
        for ln in lines:
            ln.iban = new
            ln.tags.add("decoy_old_change")
        self.reserved.add(v.id)

        owner = rng.choice([v for v in self.vendors if self._plain_vendor(v)])
        ghost = self._vendor(owner.country, owner.entities, "domestic", role="ghost")
        ghost.iban = owner.iban
        self.reserved.add(owner.id)
        self.key.append(("shared_iban", "|".join(sorted([owner.id, ghost.id]))))

    # -- invoices added on top -----------------------------------------------------
    def _added_invoices(self) -> None:
        rng = self.rng
        for v in self.split_vendors:  # three invoices in a week, each under the limit
            e = v.entities[0]
            d = _weekday(date(2026, 10, 5) + timedelta(days=rng.randint(0, 14)))
            cluster = []
            for _ in range(3):
                inv = self._new_invoice(v, e, d, lo=3400.0, hi=4800.0, role="split")
                self.invoices.append(inv)
                cluster.append(inv)
                d = _weekday(d + timedelta(days=2))
            self.key.append(("split_invoices", max(cluster, key=lambda i: (i.date, i.seq))))

        accruals = 0  # cut-off: October service invoiced in early November
        vendors = [v for v in self.vendors if self._plain_vendor(v)]
        rng.shuffle(vendors)
        for v in vendors:
            if accruals == 3:
                break
            d = _weekday(date(2026, 11, 2) + timedelta(days=rng.randint(0, 3)))
            inv = self._new_invoice(v, v.entities[0], d, spe=rng.choice(
                [date(2026, 10, 23), date(2026, 10, 30), date(2026, 10, 31)]), role="accrual")
            if not self._split_safe(inv):
                self.amounts.discard(_cents(inv.gross))
                continue
            self.invoices.append(inv)
            self.key.append(("cutoff", inv))
            accruals += 1

        for k in range(4):  # decoy: credit notes, most refunded through the bank
            orig = self._take("credit_source", lambda i: self._plain_vendor(i.vendor)
                              and date(2026, 9, 10) <= i.date <= date(2026, 10, 15) and i.qty >= 2)
            d = _weekday(orig.date + timedelta(days=rng.randint(5, 15)))
            qty = -max(1, orig.qty // 4)
            net = round(qty * orig.price, 2)
            tax = round(net * orig.rate / 100, 2)
            cn = replace(orig, seq=self._seq(), role="credit", status="credit", pay_date=None,
                         source=orig, tags={"credit_note"}, no=f"CN-{2026000 + 37 * (k + 1)}",
                         date=d, spe=d, qty=qty, net=net, tax=tax, gross=round(net + tax, 2))
            self._use(cn.gross)
            self.invoices.append(cn)
            self.reserved.add(orig.vendor.id)
            refund_day = _weekday(d + timedelta(days=rng.randint(3, 8)))
            if orig.status == "paid" and PERIOD_START <= refund_day <= LAST_BANK_DAY:
                self.lines.append(Line(seq=self._seq(), entity=cn.entity, date=refund_day,
                                       currency=cn.currency, name=self._bank_name(cn.vendor),
                                       iban=cn.vendor.iban, ref_fmt="Refund {no}", kind="refund",
                                       invs=[cn]))

    # -- other bank lines ----------------------------------------------------------
    def _oct_day(self) -> date:
        return _weekday(PERIOD_START + timedelta(days=self.rng.randint(0, 28)))

    def _other_lines(self) -> None:
        rng = self.rng

        def add(entity, currency, name, iban, ref, kind, amount, gl=True) -> Line:
            ln = Line(seq=self._seq(), entity=entity, date=self._oct_day(), currency=currency,
                      name=name, iban=iban, ref_fmt=ref, kind=kind, fixed=amount, gl=gl)
            self.lines.append(ln)
            return ln

        for e, country in ENTITIES.items():  # clean: customer receipts and bank fees, booked in GL
            for _ in range(14):
                add(e, "EUR", rng.choice(CUSTOMERS[country]), self._new_iban(country),
                    f"SO-{rng.randint(10000, 99999)}", "receipt", self._unique_amount(650, 38000))
            for _ in range(2):
                add(e, "EUR", FEE_NAMES[country], None, "", "fee", -self._unique_amount(8.5, 95))
        for _ in range(3):
            add(USD_ENTITY, "USD", rng.choice(USD_CUSTOMERS), None, f"PO {rng.randint(1000, 9999)}",
                "receipt", self._unique_amount(1200, 26000))
        add(USD_ENTITY, "USD", "Wire fees", None, "", "fee", -self._unique_amount(15, 60))

        unmatched = []  # traps: bank lines with no invoice and no GL posting
        for e in ENTITIES:
            unmatched.append(add(e, "EUR", f"TRF {rng.randint(100000, 999999)}",
                                 self._new_iban(rng.choice(["IT", "PT", "BE"])), "",
                                 "unknown", self._unique_amount(300, 9000), gl=False))
        unmatched.append(add(USD_ENTITY, "USD", "INCOMING WIRE", None, f"ORD {rng.randint(1000, 9999)}",
                             "unknown", self._unique_amount(500, 7000), gl=False))
        for e in rng.sample(list(ENTITIES), 2):
            unmatched.append(add(e, "EUR", "Card terminal fees", None, "", "fee",
                                 -self._unique_amount(20, 400), gl=False))
        vendors = [v for v in self.vendors if self._plain_vendor(v)]
        for v in rng.sample(vendors, 2):
            self.reserved.add(v.id)
            unmatched.append(add(v.entities[0], "EUR", v.name, v.iban, "Advance payment", "advance",
                                 -self._unique_amount(1200, 4800), gl=False))
        self.key += [("bank_unmatched", ln) for ln in unmatched]

    def _history_noise(self) -> None:
        """Older, legitimate bank-detail changes for a few vendors."""
        for v in self.rng.sample([v for v in self.vendors if self._plain_vendor(v)], 5):
            old = self._new_iban(v.country)
            self.history.append((v.id, old, date(self.rng.randint(2019, 2022), 3, 1), "migration"))
            self.history.append((v.id, v.iban, date(2025, self.rng.randint(1, 12), 15),
                                 self.rng.choice(APPROVERS)))

    # -- ids, POs, GL --------------------------------------------------------------
    def _assign_ids(self) -> None:
        self.invoices.sort(key=lambda i: (i.date, i.seq))
        for k, inv in enumerate(self.invoices):
            inv.id = f"D{100001 + k}"
        for e in ENTITIES:
            mine = sorted((ln for ln in self.lines if ln.entity == e), key=lambda ln: (ln.date, ln.seq))
            for k, ln in enumerate(mine):
                ln.id = f"BK-{e}-{k + 1:05d}"
        self.lines.sort(key=lambda ln: ln.id)
        n_po = n_bogus = 0
        for inv in self.invoices:
            if inv.source is not None:
                inv.po_id = inv.source.po_id
            elif inv.po_mode == "blank":
                inv.po_id = ""
            elif inv.po_mode == "bogus":
                n_bogus += 1
                inv.po_id = f"PO{990000 + n_bogus}"
            else:
                n_po += 1
                inv.po_id = f"PO{450000 + n_po}"

    def _je(self, entity: str, d: date, currency: str, text: str, rows: list[tuple]) -> None:
        self._n_je += 1
        je = f"JE{self._n_je:06d}"
        for account, debit, credit, partner in rows:
            self.gl.append([je, entity, d.isoformat(), account,
                            f"{debit:.2f}" if debit else "", f"{credit:.2f}" if credit else "",
                            currency, text, partner])

    def _build_gl(self) -> None:
        rng = self.rng
        for inv in self.invoices:
            if inv.date > PERIOD_END:
                continue  # not booked yet: that is the cut-off problem
            text = f"{inv.vendor.name} {inv.no}"
            g, t = abs(inv.gross), abs(inv.tax)
            if inv.gross > 0:
                rows = [(inv.account, round(g - t, 2), 0, ""), ("2000", 0, g, "")]
                if t:
                    rows.insert(1, ("1300", t, 0, ""))
            else:
                rows = [("2000", g, 0, ""), (inv.account, 0, round(g - t, 2), "")]
                if t:
                    rows.append(("1300", 0, t, ""))
            self._je(inv.entity, inv.date, inv.currency, text, rows)
        for ln in self.lines:
            if not ln.gl:
                continue
            bank = "1010" if ln.currency == "USD" else "1000"
            amt = ln.amount()
            other = {"payment": "2000", "refund": "2000", "receipt": "1200", "fee": "6800"}[ln.kind]
            rows = ([(bank, amt, 0, ""), (other, 0, amt, "")] if amt > 0
                    else [(other, -amt, 0, ""), (bank, 0, -amt, "")])
            self._je(ln.entity, ln.date, ln.currency, f"Bank {ln.id} {ln.name}", rows)
        for e in ENTITIES:
            amt = self._unique_amount(150000, 400000)
            self._je(e, date(2026, 10, 30), "EUR", "October sales", [("1200", amt, 0, ""), ("4000", 0, amt, "")])

        pairs = [("ES01", "DE01"), ("ES01", "FR01"), ("DE01", "FR01")]
        broken = rng.sample(pairs, 2)
        for pair in pairs:
            a, b = rng.sample(list(pair), 2)  # a charges b
            for n, what in enumerate(["Management fee 10/2026", "Shared IT services 10/2026"]):
                x = self._unique_amount(4000, 25000)
                d = _weekday(date(2026, 10, 26) + timedelta(days=rng.randint(0, 3)))
                self._je(a, d, "EUR", f"{what} to {b}", [("1500", x, 0, b), ("4000", 0, x, "")])
                if pair in broken and n == 0 and broken.index(pair) == 0:
                    x = round(x * rng.uniform(0.85, 0.95), 2)  # b booked a different amount
                elif pair in broken and n == 1 and broken.index(pair) == 1:
                    continue  # b never booked its side
                self._je(b, d, "EUR", f"{what} from {a}", [("6100", x, 0, ""), ("2500", 0, x, a)])
        for pair in broken:
            self.key.append(("intercompany_mismatch", "|".join(sorted(pair))))

    def _fx_table(self) -> None:
        d, rate = date(2026, 7, 31), 0.9180
        while d <= date(2026, 11, 30):
            if d.weekday() < 5:
                rate = round(rate + self.rng.uniform(-0.002, 0.002), 4)
                self.fx.append((d, rate))
            d += timedelta(days=1)

    def build(self) -> None:
        self._fx_table()
        self._vendors_and_invoices()
        self._payments()
        self._batch_and_fx_lines()
        self._invoice_traps()
        self._payment_traps()
        self._added_invoices()
        self._other_lines()
        self._history_noise()
        self._assign_ids()
        self._build_gl()

    # -- output --------------------------------------------------------------------
    def _amount_text(self, x: float) -> str:
        style = self.rng.random()
        if style < 0.6:
            return f"{x:.2f}"
        return f"{x:,.2f}" if style < 0.8 else _eu(x)

    def _rate_text(self, rate: float) -> str:
        s = f"{rate:g}".replace(".", ",")
        return s + "%" if self.rng.random() < 0.5 else s

    def _date_text(self, d: date | None) -> str:
        if d is None:
            return ""
        return self.rng.choice([_dmy(d, "/"), _dmy(d, "."), d.isoformat()])

    def write(self, out: Path) -> None:
        rng = self.rng
        files: dict[str, str] = {}
        rows = [["Vendor No", "Company", "Vendor Name", "Country", "VAT Reg No", "IBAN",
                 "Credit Limit", "Payment Terms"]]
        for v in self.vendors:
            for e in v.entities:
                rows.append([v.id, e, v.name, v.country, v.vat_id,
                             _spaced(v.iban) if v.spaced_iban else v.iban,
                             _eu(rng.choice([10000, 25000, 50000, 100000])), f"{v.terms} days"])
        files["vendors.csv"] = _csv(rows, ";")
        files["vendor_bank_changes.csv"] = _csv(
            [["Supplier", "New IBAN", "Changed On", "Changed By"]]
            + [[vid, iban, _dmy(d, "/"), by] for vid, iban, d, by in self.history])

        inv_rows = [["Doc ID", "Company Code", "Supplier No", "Invoice No.", "Invoice Date",
                     "Service Period End", "PO Number", "Item", "Qty", "Unit Price", "Currency",
                     "Net", "VAT %", "VAT", "Gross", "Supplier Country", "Buyer Country", "Status"]]
        po_rows, gr_rows = [], [["GR No", "PO Ref", "Received On", "Qty Recv"]]
        for inv in self.invoices:
            inv_rows.append([inv.id, inv.entity, inv.vendor.id, inv.no, self._date_text(inv.date),
                             self._date_text(inv.spe), inv.po_id, inv.item, str(inv.qty),
                             f"{inv.price:.2f}", inv.currency, self._amount_text(inv.net),
                             self._rate_text(inv.rate), self._amount_text(inv.tax),
                             self._amount_text(inv.gross), inv.vendor.country,
                             ENTITIES[inv.entity], inv.status])
            if inv.source is not None or inv.po_mode != "normal":
                continue
            po_date = inv.date - timedelta(days=rng.randint(5, 25))
            po_rows.append([inv.po_id, inv.entity, inv.vendor.id, po_date, inv.item, inv.qty,
                            inv.po_price if inv.po_price is not None else inv.price,
                            inv.currency, rng.choice(APPROVERS)])
            if not inv.has_gr:
                continue
            received = inv.qty - inv.gr_short
            gr_day = min(inv.date, po_date + timedelta(days=rng.randint(2, 10)))
            parts = [received]
            if received >= 2 and rng.random() < 0.2:
                first = rng.randint(1, received - 1)
                parts = [first, received - first]
            for q in parts:
                gr_rows.append([f"GR{len(gr_rows) + 700000}", inv.po_id, _dmy(gr_day, "."), str(q)])
        files["invoices.csv"] = _csv(inv_rows)
        files["goods_receipts.csv"] = _csv(gr_rows)

        for e in ENTITIES:
            rows = [["Company", "Account", "Booking Date", "Value Date", "Amount", "Currency",
                     "Counterparty", "Counterparty IBAN", "Remittance Information", "Transaction ID"]]
            for ln in self.lines:
                if ln.entity == e:
                    rows.append([e, f"{e}-{ln.currency}", _dmy(ln.date, "."),
                                 _dmy(_weekday(ln.date + timedelta(days=rng.choice([0, 0, 1]))), "."),
                                 _eu(ln.amount()), ln.currency, ln.name, ln.iban or "", ln.ref(), ln.id])
            files[f"bank_statement_{e}.csv"] = _csv(rows, ";")

        files["gl.csv"] = _csv([["JE Number", "Company", "Posting Date", "GL Account", "Debit",
                                 "Credit", "Curr", "Text", "Trading Partner"]] + self.gl)
        files["accounts.csv"] = _csv([["Account", "Description", "Account Type"]]
                                     + [list(a) for a in ACCOUNTS])
        files["fx.csv"] = _csv([["Date", "Currency", "EUR per Unit"]]
                               + [[d.isoformat(), "USD", f"{r:.4f}"] for d, r in self.fx])
        files["tax_rates.csv"] = _csv([["Country", "Rate", "Rate Type"]] + [list(r) for r in TAX_RATE_ROWS])
        files["contracts.json"] = json.dumps(self.contracts, indent=2, ensure_ascii=False) + "\n"

        key = []
        for check, ref in self.key:
            rid = ref if isinstance(ref, str) else ref.id
            key.append({"category": CATEGORY[check], "check": check, "record_id": rid})
        key.sort(key=lambda k: (k["category"], k["check"], k["record_id"]))
        files["answer_key.json"] = json.dumps(key, indent=2) + "\n"

        for name, text in files.items():
            (out / name).write_text(text, encoding="utf-8", newline="")
        (out / "purchase_orders.xlsx").write_bytes(self._po_xlsx(po_rows))

    @staticmethod
    def _po_xlsx(rows: list[list]) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "PO Lines"
        ws.append(["PO Number", "Company", "Supplier", "PO Date", "Material", "Ordered Qty",
                   "Net Price", "Curr", "Approver"])
        for r in rows:
            ws.append(r)
        fixed = datetime(2026, 11, 1)
        wb.properties.created = wb.properties.modified = fixed
        buf = io.BytesIO()
        wb.save(buf)
        return _stable_xlsx(buf.getvalue())


def generate(out_dir: Path, seed: int = 7, n_invoices: int = 600) -> Path:
    """Write the synthetic raw files and answer_key.json into out_dir; return out_dir.

    n_invoices is the number of base invoices; trap copies, split clusters, accruals and
    credit notes are added on top.
    """
    if n_invoices < MIN_INVOICES:
        raise ValueError(f"n_invoices must be at least {MIN_INVOICES}, got {n_invoices}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    builder = _Builder(seed, n_invoices)
    builder.build()
    builder.write(out)
    return out
