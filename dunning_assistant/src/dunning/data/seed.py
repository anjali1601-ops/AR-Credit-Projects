"""Deterministic synthetic AR dataset: customers, invoices, payments, promises, email threads.

Every record is generated relative to ``settings.as_of`` so aging buckets and
"days since last contact" metrics never drift with the wall clock.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..config import Settings, get_settings

TABLES = ("customers", "invoices", "payments", "promises", "emails")


@dataclass(frozen=True)
class CustomerSpec:
    account_id: str
    name: str
    industry: str
    segment: str
    archetype: str
    acv: float
    credit_limit: float
    terms: int
    late_fee_pct: float
    contract_clause: str
    ar_owner: str
    contact_name: str
    contact_email: str
    contact_phone: str
    tenure_months: int
    paid_invoices: int
    open_dpd: tuple[int, ...]
    base_amount: float
    disputed_open: int = 0
    partial_on_oldest: float = 0.0
    # Email thread shape; defaults to the archetype's typical thread.
    email_profile: str = ""


SPECS: list[CustomerSpec] = [
    # --- reliable but chronically late: predictable AP cycle, warm relationship ---
    CustomerSpec(
        account_id="ACC-1001",
        name="Harbor Point Logistics",
        industry="Freight & Logistics",
        segment="mid_market",
        archetype="reliable_but_late",
        acv=186_000,
        credit_limit=90_000,
        terms=30,
        late_fee_pct=1.5,
        contract_clause="MSA §7.3 (Payment Terms)",
        ar_owner="Priya Raman",
        contact_name="Dana Whitfield",
        contact_email="dana.whitfield@harborpointlog.com",
        contact_phone="+1-503-555-0142",
        tenure_months=34,
        paid_invoices=18,
        open_dpd=(24,),
        base_amount=15_500,
    ),
    CustomerSpec(
        account_id="ACC-1002",
        name="Cobalt Dental Group",
        industry="Healthcare Services",
        segment="smb",
        archetype="reliable_but_late",
        acv=64_800,
        credit_limit=30_000,
        terms=30,
        late_fee_pct=1.0,
        contract_clause="Order Form §4.2 (Fees)",
        ar_owner="Marcus Idowu",
        contact_name="Renee Alvarado",
        contact_email="renee.alvarado@cobaltdental.com",
        contact_phone="+1-312-555-0117",
        tenure_months=26,
        paid_invoices=15,
        open_dpd=(17,),
        base_amount=5_400,
    ),
    CustomerSpec(
        account_id="ACC-1003",
        name="Ridgeway Print Co.",
        industry="Commercial Printing",
        segment="smb",
        archetype="reliable_but_late",
        acv=48_000,
        credit_limit=25_000,
        terms=45,
        late_fee_pct=1.0,
        contract_clause="Service Agreement §5.1 (Invoicing)",
        ar_owner="Priya Raman",
        contact_name="Tom Beckett",
        contact_email="tbeckett@ridgewayprint.com",
        contact_phone="+1-414-555-0188",
        tenure_months=41,
        paid_invoices=20,
        open_dpd=(31,),
        base_amount=4_000,
        email_profile="reliable_frustrated",
    ),
    # --- deteriorating / avoidant: lateness trending up, replies drying out ---
    CustomerSpec(
        account_id="ACC-2001",
        name="Northwind Interiors",
        industry="Commercial Interiors",
        segment="mid_market",
        archetype="deteriorating_avoidant",
        acv=240_000,
        credit_limit=120_000,
        terms=30,
        late_fee_pct=1.5,
        contract_clause="MSA §7.3 (Payment Terms)",
        ar_owner="Marcus Idowu",
        contact_name="Gavin Mercer",
        contact_email="g.mercer@northwindinteriors.com",
        contact_phone="+1-206-555-0164",
        tenure_months=29,
        paid_invoices=14,
        open_dpd=(68, 38, 11),
        base_amount=20_000,
    ),
    CustomerSpec(
        account_id="ACC-2002",
        name="Solstice Media Labs",
        industry="Marketing Technology",
        segment="mid_market",
        archetype="deteriorating_avoidant",
        acv=150_000,
        credit_limit=75_000,
        terms=30,
        late_fee_pct=1.5,
        contract_clause="MSA §9.4 (Late Payment)",
        ar_owner="Priya Raman",
        contact_name="Elise Fontaine",
        contact_email="elise@solsticemedialabs.io",
        contact_phone="+1-646-555-0133",
        tenure_months=22,
        paid_invoices=11,
        open_dpd=(66, 44, 14),
        base_amount=12_500,
    ),
    CustomerSpec(
        account_id="ACC-2003",
        name="Brightline Staffing",
        industry="Staffing & Recruiting",
        segment="smb",
        archetype="deteriorating_avoidant",
        acv=96_000,
        credit_limit=45_000,
        terms=30,
        late_fee_pct=1.25,
        contract_clause="Order Form §4.2 (Fees)",
        ar_owner="Marcus Idowu",
        contact_name="Sam Okoro",
        contact_email="sokoro@brightlinestaffing.com",
        contact_phone="+1-704-555-0125",
        tenure_months=19,
        paid_invoices=10,
        open_dpd=(61, 33),
        base_amount=8_000,
    ),
    # --- high-risk delinquent: deep aging, broken promises, hostile or silent ---
    CustomerSpec(
        account_id="ACC-3001",
        name="Apex Modular Build",
        industry="Construction",
        segment="mid_market",
        archetype="high_risk_delinquent",
        acv=310_000,
        credit_limit=110_000,
        terms=30,
        late_fee_pct=1.5,
        contract_clause="MSA §11.2 (Default & Remedies)",
        ar_owner="Priya Raman",
        contact_name="Curtis Hale",
        contact_email="c.hale@apexmodular.build",
        contact_phone="+1-713-555-0199",
        tenure_months=33,
        paid_invoices=12,
        open_dpd=(127, 96, 68, 39),
        base_amount=26_000,
        disputed_open=1,
        partial_on_oldest=0.22,
    ),
    CustomerSpec(
        account_id="ACC-3002",
        name="Hollowbrook Retail Group",
        industry="Specialty Retail",
        segment="mid_market",
        archetype="high_risk_delinquent",
        acv=198_000,
        credit_limit=80_000,
        terms=45,
        late_fee_pct=1.5,
        contract_clause="MSA §11.2 (Default & Remedies)",
        ar_owner="Marcus Idowu",
        contact_name="Lena Pruitt",
        contact_email="lpruitt@hollowbrookretail.com",
        contact_phone="+1-901-555-0171",
        tenure_months=27,
        paid_invoices=10,
        open_dpd=(151, 108, 74),
        base_amount=17_500,
        email_profile="high_risk_hostile_recent",
    ),
    CustomerSpec(
        account_id="ACC-3003",
        name="Tidewater Freight Partners",
        industry="Freight & Logistics",
        segment="smb",
        archetype="high_risk_delinquent",
        acv=84_000,
        credit_limit=35_000,
        terms=30,
        late_fee_pct=1.5,
        contract_clause="Service Agreement §8.5 (Collections)",
        ar_owner="Priya Raman",
        contact_name="Roy Stapleton",
        contact_email="roy@tidewaterfreight.net",
        contact_phone="+1-757-555-0154",
        tenure_months=16,
        paid_invoices=8,
        open_dpd=(118, 88, 57),
        base_amount=7_200,
        disputed_open=1,
        partial_on_oldest=0.15,
    ),
]


def _rng_for(account_id: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"{account_id}:{seed}".encode()).hexdigest()[:12]
    return random.Random(int(digest, 16))


def _money(value: float) -> float:
    return round(value, 2)


def _late_history(spec: CustomerSpec, rng: random.Random) -> list[int]:
    """Days-late pattern for historical (paid) invoices, oldest first."""
    n = spec.paid_invoices
    if spec.archetype == "reliable_but_late":
        centre = 15 if spec.terms == 30 else 13
        return [max(6, int(rng.gauss(centre, 2.2))) for _ in range(n)]
    if spec.archetype == "deteriorating_avoidant":
        history: list[int] = []
        for k in range(n):
            progress = k / max(1, n - 1)
            centre = 4 + progress * 34
            history.append(max(0, int(rng.gauss(centre, 3.5))))
        return history
    # high risk: erratic, occasionally very late from early on
    history = []
    for k in range(n):
        if rng.random() < 0.35:
            history.append(rng.randint(48, 92))
        else:
            history.append(rng.randint(3, 34))
    return history


def _build_invoices(spec: CustomerSpec, rng: random.Random, as_of: date):
    invoices, payments = [], []
    oldest_open_dpd = max(spec.open_dpd)
    oldest_open_due = as_of - timedelta(days=oldest_open_dpd)
    oldest_open_issue = oldest_open_due - timedelta(days=spec.terms)

    history = _late_history(spec, rng)
    seq = 1
    for k, days_late in enumerate(history):
        months_back = len(history) - k
        issue = oldest_open_issue - timedelta(days=30 * months_back + rng.randint(-3, 3))
        due = issue + timedelta(days=spec.terms)
        amount = _money(spec.base_amount * rng.uniform(0.82, 1.24))
        paid_on = due + timedelta(days=days_late)
        invoice_id = f"INV-{spec.account_id[-4:]}-{seq:03d}"
        invoices.append(
            dict(
                invoice_id=invoice_id,
                account_id=spec.account_id,
                issue_date=issue,
                due_date=due,
                amount=amount,
                amount_paid=amount,
                status="paid",
                paid_date=paid_on,
                days_late=days_late,
                disputed=False,
                po_number=f"PO-{rng.randint(41000, 98999)}",
                description=rng.choice(
                    ["Monthly platform subscription", "Managed services retainer", "Usage overage", "Implementation services"]
                ),
            )
        )
        payments.append(
            dict(
                payment_id=f"PAY-{spec.account_id[-4:]}-{seq:03d}",
                account_id=spec.account_id,
                invoice_id=invoice_id,
                payment_date=paid_on,
                amount=amount,
                method=rng.choice(["ACH", "ACH", "check", "wire"]),
            )
        )
        seq += 1

    open_sorted = sorted(spec.open_dpd, reverse=True)
    for idx, dpd in enumerate(open_sorted):
        due = as_of - timedelta(days=dpd)
        issue = due - timedelta(days=spec.terms)
        amount = _money(spec.base_amount * rng.uniform(0.9, 1.35))
        invoice_id = f"INV-{spec.account_id[-4:]}-{seq:03d}"
        paid_so_far = 0.0
        status = "open"
        if idx == 0 and spec.partial_on_oldest:
            paid_so_far = _money(amount * spec.partial_on_oldest)
            status = "partial"
            payments.append(
                dict(
                    payment_id=f"PAY-{spec.account_id[-4:]}-{seq:03d}",
                    account_id=spec.account_id,
                    invoice_id=invoice_id,
                    payment_date=due + timedelta(days=int(dpd * 0.45)),
                    amount=paid_so_far,
                    method="ACH",
                )
            )
        invoices.append(
            dict(
                invoice_id=invoice_id,
                account_id=spec.account_id,
                issue_date=issue,
                due_date=due,
                amount=amount,
                amount_paid=paid_so_far,
                status=status,
                paid_date=None,
                days_late=None,
                disputed=bool(spec.disputed_open and idx == 1),
                po_number=f"PO-{rng.randint(41000, 98999)}",
                description=rng.choice(
                    ["Monthly platform subscription", "Managed services retainer", "Usage overage", "Professional services"]
                ),
            )
        )
        seq += 1
    return invoices, payments


def _build_promises(spec: CustomerSpec, rng: random.Random, as_of: date, open_invoice_ids: list[str]):
    promises = []
    if spec.archetype == "reliable_but_late":
        plan = [(9, 4, True)]
    elif spec.archetype == "deteriorating_avoidant":
        plan = [(58, 44, True), (36, 22, False)]
    else:
        plan = [(102, 88, False), (74, 60, False), (41, 27, False)]

    for idx, (made_days_ago, due_days_ago, kept) in enumerate(plan):
        invoice_id = open_invoice_ids[min(idx, len(open_invoice_ids) - 1)]
        promises.append(
            dict(
                promise_id=f"PTP-{spec.account_id[-4:]}-{idx + 1:02d}",
                account_id=spec.account_id,
                invoice_id=invoice_id,
                promised_on=as_of - timedelta(days=made_days_ago),
                promised_date=as_of - timedelta(days=due_days_ago),
                promised_amount=_money(spec.base_amount * rng.uniform(0.4, 1.0)),
                kept=kept,
                source=rng.choice(["email", "phone"]),
            )
        )
    return promises


RELIABLE_INBOUND = [
    "Hi {rep}, thanks for the nudge and apologies for the delay. Our AP run goes out on the 15th, so {invoice} will be in that batch. Appreciate your patience as always.",
    "Thanks {rep} - payment is approved on our side, it just has to clear the weekly check run. Should land with you early next week. Always a pleasure working with your team.",
    "Got it, thank you for flagging. I've pushed {invoice} through for approval today. Sorry we're always a couple of weeks behind our terms, our finance cycle is stubborn.",
]

RELIABLE_OUTBOUND = [
    "Hi {contact}, hope things are well. Quick reminder that {invoice} for {amount} is now {dpd} days past due. Happy to resend the PDF if that helps.",
    "Hi {contact}, just a friendly check-in on {invoice} ({amount}). Let me know if anything is blocking approval on your side.",
]

DETERIORATING_INBOUND_EARLY = [
    "Thanks {rep}, we'll get this processed this week. Sorry for the back and forth.",
    "Appreciate the reminder - I've forwarded {invoice} to our controller for sign-off.",
]

DETERIORATING_INBOUND_MID = [
    "Apologies, I'm still waiting on our controller to sign off. I'll chase it again and come back to you.",
    "Sorry, things are hectic here right now. Let me check with finance and revert next week.",
    "We're reorganising our AP process, so this is taking longer than usual. I'll follow up once I know more.",
]

DETERIORATING_OUTBOUND = [
    "Hi {contact}, following up on {invoice} for {amount}, now {dpd} days past due. Can you confirm a payment date?",
    "Hi {contact}, checking in again on the outstanding balance of {balance}. I haven't heard back on my last note - is there an internal blocker we can help with?",
    "Hi {contact}, I wanted to make sure this didn't get lost. {invoice} remains unpaid at {dpd} days past due. Could we get 10 minutes this week?",
    "Hi {contact}, third attempt on the past-due balance of {balance}. I'd rather sort this with you directly than escalate internally.",
]

HIGH_RISK_INBOUND = [
    "Frankly, your invoicing has been a mess for months and nobody responds to our questions. We are not releasing any payment until this is sorted out.",
    "We dispute {invoice} entirely - the scope was never delivered as promised. Stop sending these automated reminders, they are unhelpful and frustrating.",
    "This is the third time I've explained that cash is extremely tight. Pressuring us weekly is not going to change that. I'm losing patience with this process.",
]

RELIABLE_FRUSTRATED_INBOUND = [
    "{rep}, this is the second time we've been billed for the wrong amount on this account. It's frustrating, and honestly it's why payment is sitting unapproved. Please get the credit issued.",
    "Thanks for looking into it. We've always paid you, but the invoicing errors are making this harder than it needs to be. Once the corrected {invoice} lands I'll push it through our next AP run.",
]

HIGH_RISK_OUTBOUND = [
    "Hi {contact}, the past-due balance on your account is {balance}, with the oldest invoice {dpd} days past due. We need a written payment commitment.",
    "Hi {contact}, following up on our call. The promised payment did not arrive. Please confirm today when funds will be sent.",
    "Hi {contact}, your account remains significantly past due at {balance}. Per {clause}, late fees of {fee}% per month now apply.",
    "Hi {contact}, we have not received a response to our last three messages regarding {balance}. Please treat this as urgent.",
    "Hi {contact}, unless we receive payment or a signed plan, this account will be referred for further action under {clause}.",
]


def _fmt_money(value: float) -> str:
    return f"${value:,.0f}"


def _build_emails(spec: CustomerSpec, rng: random.Random, as_of: date, invoices: list[dict]):
    open_invoices = [inv for inv in invoices if inv["status"] != "paid"]
    oldest = open_invoices[0]
    balance = sum(inv["amount"] - inv["amount_paid"] for inv in open_invoices)
    ctx = dict(
        rep=spec.ar_owner.split()[0],
        contact=spec.contact_name.split()[0],
        invoice=oldest["invoice_id"],
        amount=_fmt_money(oldest["amount"]),
        balance=_fmt_money(balance),
        dpd=(as_of - oldest["due_date"]).days,
        clause=spec.contract_clause,
        fee=f"{spec.late_fee_pct:g}",
    )
    thread_id = f"THR-{spec.account_id[-4:]}-01"
    messages: list[tuple[int, str, str, str]] = []  # days_ago, direction, subject, body

    profile = spec.email_profile or spec.archetype

    if profile == "reliable_frustrated":
        subject = f"Invoice {oldest['invoice_id']} - billing discrepancy"
        messages = [
            (23, "outbound", subject, RELIABLE_OUTBOUND[0]),
            (21, "inbound", f"RE: {subject}", RELIABLE_FRUSTRATED_INBOUND[0]),
            (12, "outbound", subject, RELIABLE_OUTBOUND[1]),
            (5, "inbound", f"RE: {subject}", RELIABLE_FRUSTRATED_INBOUND[1]),
        ]
    elif profile == "high_risk_hostile_recent":
        subject = f"URGENT: past-due balance {ctx['balance']}"
        messages = [
            (140, "outbound", subject, HIGH_RISK_OUTBOUND[0]),
            (136, "inbound", f"RE: {subject}", HIGH_RISK_INBOUND[2]),
            (112, "outbound", subject, HIGH_RISK_OUTBOUND[1]),
            (95, "outbound", subject, HIGH_RISK_OUTBOUND[2]),
            (60, "outbound", subject, HIGH_RISK_OUTBOUND[3]),
            (31, "outbound", subject, HIGH_RISK_OUTBOUND[0]),
            (10, "outbound", subject, HIGH_RISK_OUTBOUND[4]),
            (6, "inbound", f"RE: {subject}", HIGH_RISK_INBOUND[0]),
        ]
    elif profile == "reliable_but_late":
        subject = f"Invoice {oldest['invoice_id']} - payment status"
        messages = [
            (21, "outbound", subject, RELIABLE_OUTBOUND[0]),
            (19, "inbound", f"RE: {subject}", RELIABLE_INBOUND[0]),
            (11, "outbound", subject, RELIABLE_OUTBOUND[1]),
            (9, "inbound", f"RE: {subject}", RELIABLE_INBOUND[1]),
            (4, "outbound", subject, RELIABLE_OUTBOUND[0]),
            (3, "inbound", f"RE: {subject}", RELIABLE_INBOUND[2]),
        ]
    elif profile == "deteriorating_avoidant":
        subject = f"Past due balance - {spec.name}"
        messages = [
            (96, "outbound", subject, DETERIORATING_OUTBOUND[0]),
            (94, "inbound", f"RE: {subject}", DETERIORATING_INBOUND_EARLY[0]),
            (72, "outbound", subject, DETERIORATING_OUTBOUND[1]),
            (69, "inbound", f"RE: {subject}", DETERIORATING_INBOUND_EARLY[1]),
            (55, "outbound", subject, DETERIORATING_OUTBOUND[2]),
            (52, "inbound", f"RE: {subject}", DETERIORATING_INBOUND_MID[0]),
            (38, "outbound", subject, DETERIORATING_OUTBOUND[1]),
            (36, "inbound", f"RE: {subject}", DETERIORATING_INBOUND_MID[rng.randint(1, 2)]),
            (24, "outbound", subject, DETERIORATING_OUTBOUND[3]),
            (13, "outbound", subject, DETERIORATING_OUTBOUND[2]),
            (5, "outbound", subject, DETERIORATING_OUTBOUND[1]),
        ]
    else:
        subject = f"URGENT: past-due balance {ctx['balance']}"
        messages = [
            (132, "outbound", subject, HIGH_RISK_OUTBOUND[0]),
            (129, "inbound", f"RE: {subject}", HIGH_RISK_INBOUND[2]),
            (110, "outbound", subject, HIGH_RISK_OUTBOUND[1]),
            (104, "inbound", f"RE: {subject}", HIGH_RISK_INBOUND[0]),
            (88, "outbound", subject, HIGH_RISK_OUTBOUND[2]),
            (81, "inbound", f"RE: {subject}", HIGH_RISK_INBOUND[1]),
            (63, "outbound", subject, HIGH_RISK_OUTBOUND[3]),
            (44, "outbound", subject, HIGH_RISK_OUTBOUND[0]),
            (27, "outbound", subject, HIGH_RISK_OUTBOUND[4]),
            (12, "outbound", subject, HIGH_RISK_OUTBOUND[3]),
        ]

    rows = []
    for idx, (days_ago, direction, subject_line, template) in enumerate(messages):
        author = spec.ar_owner if direction == "outbound" else spec.contact_name
        rows.append(
            dict(
                message_id=f"MSG-{spec.account_id[-4:]}-{idx + 1:02d}",
                account_id=spec.account_id,
                thread_id=thread_id,
                sent_at=as_of - timedelta(days=days_ago),
                direction=direction,
                author=author,
                subject=subject_line,
                body=template.format(**ctx),
            )
        )
    return rows


def build_dataset(settings: Settings | None = None) -> dict[str, pd.DataFrame]:
    settings = settings or get_settings()
    as_of = settings.as_of
    customers, invoices, payments, promises, emails = [], [], [], [], []

    for spec in SPECS:
        rng = _rng_for(spec.account_id, settings.seed)
        customers.append(
            dict(
                account_id=spec.account_id,
                name=spec.name,
                industry=spec.industry,
                segment=spec.segment,
                relationship_start=as_of - timedelta(days=30 * spec.tenure_months),
                annual_contract_value=spec.acv,
                credit_limit=spec.credit_limit,
                payment_terms_days=spec.terms,
                late_fee_pct=spec.late_fee_pct,
                contract_clause=spec.contract_clause,
                ar_owner=spec.ar_owner,
                contact_name=spec.contact_name,
                contact_email=spec.contact_email,
                contact_phone=spec.contact_phone,
                archetype=spec.archetype,
            )
        )
        acct_invoices, acct_payments = _build_invoices(spec, rng, as_of)
        open_ids = [inv["invoice_id"] for inv in acct_invoices if inv["status"] != "paid"]
        invoices.extend(acct_invoices)
        payments.extend(acct_payments)
        promises.extend(_build_promises(spec, rng, as_of, open_ids))
        emails.extend(_build_emails(spec, rng, as_of, acct_invoices))

    frames = {
        "customers": pd.DataFrame(customers),
        "invoices": pd.DataFrame(invoices),
        "payments": pd.DataFrame(payments),
        "promises": pd.DataFrame(promises),
        "emails": pd.DataFrame(emails),
    }
    for name, frame in frames.items():
        for column in frame.columns:
            if column.endswith("_date") or column in {"sent_at", "relationship_start", "promised_on"}:
                frames[name][column] = pd.to_datetime(frame[column]).dt.date
    return frames


def write_dataset(settings: Settings | None = None) -> Path:
    """Write CSVs plus a SQLite mirror; returns the data directory."""
    settings = settings or get_settings()
    frames = build_dataset(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    for name, frame in frames.items():
        frame.to_csv(settings.data_dir / f"{name}.csv", index=False)

    with sqlite3.connect(settings.sqlite_path) as conn:
        for name, frame in frames.items():
            frame.astype({c: "str" for c in frame.columns if frame[c].dtype == "object"}).to_sql(
                name, conn, if_exists="replace", index=False
            )

    meta = {
        "as_of": settings.as_of.isoformat(),
        "seed": settings.seed,
        "accounts": int(len(frames["customers"])),
        "archetypes": sorted(frames["customers"]["archetype"].unique().tolist()),
        "row_counts": {name: int(len(frame)) for name, frame in frames.items()},
    }
    (settings.data_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return settings.data_dir
