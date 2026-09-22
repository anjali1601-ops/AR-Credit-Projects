"""Harborline Industrial Supply — open AR and eight remittances.

The documents below are the raw lockbox stubs, EDI 820s, and short-pay
emails the ingestion agent reads. Expected outcomes live in the tests,
not in this file, so a parser change cannot silently agree with itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cash_application.ingest import parse_remittance
from cash_application.matcher import CustomerView, InvoiceView, propose
from cash_application.money import ZERO, money


@dataclass(frozen=True)
class CustomerSeed:
    account_number: str
    name: str
    aliases: tuple[str, ...]
    city: str
    state: str
    payment_terms: str


@dataclass(frozen=True)
class InvoiceSeed:
    invoice_number: str
    account_number: str
    po_number: str | None
    issue_date: dt.date
    due_date: dt.date
    amount: str
    status: str
    description: str


@dataclass(frozen=True)
class RemittanceSeed:
    external_ref: str
    channel: str
    received_on: dt.date
    raw_text: str


CUSTOMERS: tuple[CustomerSeed, ...] = (
    CustomerSeed(
        "NW-10042",
        "Northwind Retail Co.",
        ("NORTHWIND RETAIL", "Northwind Retail"),
        "Chicago",
        "IL",
        "Net 30",
    ),
    CustomerSeed(
        "CH-22018",
        "Cascade Health Systems",
        ("CASCADE HEALTH", "Cascade Health"),
        "Portland",
        "OR",
        "Net 30",
    ),
    CustomerSeed(
        "VM-30881",
        "Vertex Manufacturing LLC",
        ("VERTEX MANUFACTURING", "Vertex Mfg"),
        "Detroit",
        "MI",
        "Net 45",
    ),
    CustomerSeed(
        "PG-44102",
        "Pinnacle Grocers Inc.",
        ("PINNACLE GROCERS", "Pinnacle Grocers"),
        "Dallas",
        "TX",
        "Net 30",
    ),
    CustomerSeed(
        "RM-55077",
        "Redwood Municipal Utilities",
        ("REDWOOD MUNICIPAL", "Redwood Utilities"),
        "Eureka",
        "CA",
        "Net 45",
    ),
    CustomerSeed(
        "LA-61290",
        "Lakeshore Automotive Group",
        ("LAKESHORE AUTOMOTIVE", "Lakeshore Auto"),
        "Cleveland",
        "OH",
        "Net 30",
    ),
)

INVOICES: tuple[InvoiceSeed, ...] = (
    InvoiceSeed("INV-10481", "NW-10042", "NW-PO-8831", dt.date(2026, 8, 18), dt.date(2026, 9, 17), "4250.00", "open", "Nitrile gloves, case pack, DC replenishment"),
    InvoiceSeed("INV-10492", "NW-10042", "NW-PO-8902", dt.date(2026, 8, 25), dt.date(2026, 9, 24), "1875.50", "open", "Janitorial can liners, three DCs"),
    InvoiceSeed("INV-10503", "NW-10042", "NW-PO-9010", dt.date(2026, 9, 2), dt.date(2026, 10, 2), "6400.00", "open", "Shelf-ready corrugated displays"),
    InvoiceSeed("INV-10510", "CH-22018", "CH-PO-4419", dt.date(2026, 8, 12), dt.date(2026, 9, 11), "12480.00", "open", "Exam gloves and gauze, clinic standing order"),
    InvoiceSeed("INV-10522", "CH-22018", "CH-PO-4502", dt.date(2026, 9, 1), dt.date(2026, 10, 1), "3260.75", "open", "Sharps containers, 8-gallon"),
    InvoiceSeed("INV-10530", "VM-30881", "VM-77421", dt.date(2026, 8, 15), dt.date(2026, 9, 29), "8900.00", "open", "Stainless fittings for line 4"),
    InvoiceSeed("INV-10531", "VM-30881", "VM-77455", dt.date(2026, 8, 15), dt.date(2026, 9, 29), "2150.00", "open", "Valve kits, stainless, line 4"),
    InvoiceSeed("INV-10544", "VM-30881", "VM-77001", dt.date(2026, 7, 2), dt.date(2026, 8, 16), "940.00", "paid", "Gasket kit, already settled"),
    InvoiceSeed("INV-10550", "PG-44102", "PG-33190", dt.date(2026, 8, 20), dt.date(2026, 9, 19), "5600.00", "open", "Produce film and scale labels"),
    InvoiceSeed("INV-10558", "PG-44102", "PG-33240", dt.date(2026, 9, 4), dt.date(2026, 10, 4), "2225.40", "open", "Bakery bags, seasonal print"),
    InvoiceSeed("INV-10570", "RM-55077", "RMU-2026-09", dt.date(2026, 8, 28), dt.date(2026, 10, 12), "15000.00", "open", "Transformer gaskets and seals"),
    InvoiceSeed("INV-10571", "RM-55077", "RMU-2026-10", dt.date(2026, 9, 5), dt.date(2026, 10, 20), "7333.33", "open", "Substation hardware kit"),
    InvoiceSeed("INV-10580", "LA-61290", "LA-18820", dt.date(2026, 8, 22), dt.date(2026, 9, 21), "1100.00", "open", "Brake hardware assortment"),
    InvoiceSeed("INV-10581", "LA-61290", "LA-18844", dt.date(2026, 8, 22), dt.date(2026, 9, 21), "450.25", "open", "Shop towels and abrasives"),
)

_EXACT = """\
LOCKBOX REMITTANCE ADVICE
Depository: First Midwest Bank, Lockbox 8841
Batch: LBX-20260918-014
Deposit date: 2026-09-18
Payer: NORTHWIND RETAIL CO
Account: NW-10042
Check number: 882104
Check amount: 4250.00
Invoices: INV-10481
PO: NW-PO-8831
Memo: Payment for INV-10481
"""

_MULTI = """\
ISA*00*          *00*          *ZZ*VERTEXMFG      *ZZ*HARBORLINE     *260918*1200*U*00401*000000121*0*P*:~
GS*RA*VERTEXMFG*HARBORLINE*20260918*1200*121*X*004010~
ST*820*0001~
BPR*C*11050.00*C*ACH*CTX*01*021000021*DA*123456789*9876543210*01*071000013*DA*555019900*20260918~
TRN*1*820-20260918-VM*121140399~
REF*AC*VM-30881~
N1*PR*VERTEX MANUFACTURING LLC~
N1*PE*HARBORLINE INDUSTRIAL SUPPLY~
ENT*1~
RMR*IV*INV-10530**8900.00~
REF*PO*VM-77421~
RMR*IV*INV-10531**2150.00~
REF*PO*VM-77455~
SE*14*0001~
GE*1*121~
IEA*1*000000121~
"""

_SHORT = """\
From: Accounts Payable <ap@cascadehealth.example>
To: cashapp@harborline.example
Subject: Short pay on INV-10510 — damaged cartons
Date: Fri, 19 Sep 2026 09:14:00 -0700

Payer: Cascade Health Systems
Account: CH-22018
Remitting: $11,240.00
Invoices: INV-10510
PO: CH-PO-4419
Deduction: $1,240.00 damaged cartons, POD exception on PO CH-PO-4419

We are paying invoice INV-10510 short. Twenty-seven cartons of exam
gloves arrived crushed. Receiving refused them and the POD is noted.
Please apply $11,240.00 and leave the disputed $1,240.00 open.
"""

_OVER = """\
LOCKBOX REMITTANCE ADVICE
Depository: First Midwest Bank, Lockbox 8841
Batch: LBX-20260918-088
Deposit date: 2026-09-18
Payer: PINNACLE GROCERS INC.
Account: PG-44102
Check number: 441902
Check amount: 6100.00
Invoices: INV-10550
PO: PG-33190
Memo: INV-10550 plus extra to leave on the account
"""

_UNAPPLIED = """\
LOCKBOX REMITTANCE ADVICE
Depository: First Midwest Bank, Lockbox 8841
Batch: LBX-20260918-102
Deposit date: 2026-09-18
Payer: APEX SURPLUS LIQUIDATORS
Account:
Check number: 904418
Check amount: 2000.00
Invoices:
PO:
Memo: No invoice stub in the envelope
"""

_REFERENCE = """\
ISA*00*          *00*          *ZZ*REDWOODUTIL    *ZZ*HARBORLINE     *260918*1400*U*00401*000000122*0*P*:~
GS*RA*REDWOODUTIL*HARBORLINE*20260918*1400*122*X*004010~
ST*820*0001~
BPR*C*15000.00*C*ACH*CTX*01*121000248*DA*111222333*4445556666*01*071000013*DA*555019900*20260918~
TRN*1*820-20260918-RMU*121140399~
REF*AC*RM-55077~
N1*PR*REDWOOD MUNICIPAL UTILITIES~
N1*PE*HARBORLINE INDUSTRIAL SUPPLY~
REF*PO*RMU-2026-09~
SE*9*0001~
GE*1*122~
IEA*1*000000122~
"""

_MULTI_SHORT = """\
From: Lakeshore Automotive AP <ap@lakeshoreauto.example>
To: cashapp@harborline.example
Subject: Short pay INV-10580 and INV-10581
Date: Sun, 20 Sep 2026 16:02:00 -0400

Payer: Lakeshore Automotive Group
Account: LA-61290
Remitting: $1,500.00
Invoices: INV-10580, INV-10581
PO: LA-18820
Deduction: $50.25 shortage on the brake hardware, invoice INV-10581

Please apply $1,100.00 to INV-10580 and $400.00 to INV-10581.
The remaining $50.25 on INV-10581 stays open until we count the bins.
"""

_AMOUNT_ONLY = """\
LOCKBOX REMITTANCE ADVICE
Depository: First Midwest Bank, Lockbox 8841
Batch: LBX-20260921-221
Deposit date: 2026-09-21
Payer: NORTHWIND RETAIL CO
Account: NW-10042
Check number: 882311
Check amount: 1875.50
Invoices:
PO:
Memo: On account NW-10042
"""

REMITTANCES: tuple[RemittanceSeed, ...] = (
    RemittanceSeed("LBX-20260918-014", "lockbox", dt.date(2026, 9, 18), _EXACT),
    RemittanceSeed("EDI-820-20260918-VM", "edi_820", dt.date(2026, 9, 18), _MULTI),
    RemittanceSeed("EML-20260919-CASCADE", "email", dt.date(2026, 9, 19), _SHORT),
    RemittanceSeed("LBX-20260918-088", "lockbox", dt.date(2026, 9, 18), _OVER),
    RemittanceSeed("LBX-20260918-102", "lockbox", dt.date(2026, 9, 18), _UNAPPLIED),
    RemittanceSeed("EDI-820-20260918-RMU", "edi_820", dt.date(2026, 9, 18), _REFERENCE),
    RemittanceSeed("EML-20260920-LAKESHORE", "email", dt.date(2026, 9, 20), _MULTI_SHORT),
    RemittanceSeed("LBX-20260921-221", "lockbox", dt.date(2026, 9, 21), _AMOUNT_ONLY),
)

# The five application shapes the demo has to keep apart.
PRIMARY_REFS: tuple[str, ...] = (
    "LBX-20260918-014",
    "EDI-820-20260918-VM",
    "EML-20260919-CASCADE",
    "LBX-20260918-088",
    "LBX-20260918-102",
)

_BY_REF = {item.external_ref: item for item in REMITTANCES}


def customer_views() -> list[CustomerView]:
    return [
        CustomerView(account_number=item.account_number, name=item.name, aliases=item.aliases)
        for item in CUSTOMERS
    ]


def invoice_views() -> list[InvoiceView]:
    names = {item.account_number: item.name for item in CUSTOMERS}
    views: list[InvoiceView] = []
    for item in INVOICES:
        open_amount = ZERO if item.status == "paid" else money(item.amount)
        views.append(
            InvoiceView(
                invoice_number=item.invoice_number,
                customer_account=item.account_number,
                customer_name=names[item.account_number],
                po_number=item.po_number,
                open_amount=open_amount,
                status=item.status,
            )
        )
    return views


def run_case(external_ref: str):
    """Parse, match, and route one seeded remittance. No database and no model."""
    from cash_application.exceptions import route

    item = _BY_REF[external_ref]
    extraction = parse_remittance(item.channel, item.raw_text)
    match = propose(extraction, invoice_views(), customer_views())
    return extraction, match, route(match)
