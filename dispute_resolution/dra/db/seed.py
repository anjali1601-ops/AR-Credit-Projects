"""Seed a small but realistic ERP, index the contracts, and fill the inbox.

Dates are relative to today so the contractual notice windows stay meaningful
however long after the seed you run the demo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from dra.db.models import (
    Contract,
    Customer,
    DiscountAgreement,
    Invoice,
    InvoiceLine,
    Payment,
    Shipment,
    ShipmentLine,
)
from dra.db.session import init_db, session_scope
from dra.inbox.simulator import load_samples
from dra.rag.contracts import build_contract_index, load_sources


def D(value: str | float) -> Decimal:  # noqa: N802 - terse on purpose in fixtures
    return Decimal(str(value)).quantize(Decimal("0.01"))


def seed_customers(session: Session, today: dt.date) -> dict[str, Customer]:
    rows = [
        Customer(
            code="CUST-1001",
            name="Northwind Retail Group",
            segment="retail",
            email_domain="northwind-retail.example",
            ar_contact_name="Dana Whitfield",
            ar_contact_email="ap@northwind-retail.example",
            payment_terms="NET30",
            credit_limit=D(250_000),
        ),
        Customer(
            code="CUST-1002",
            name="Cascade Health Supply",
            segment="healthcare",
            email_domain="cascadehealth.example",
            ar_contact_name="Marcus Bell",
            ar_contact_email="payables@cascadehealth.example",
            payment_terms="NET45",
            credit_limit=D(400_000),
        ),
        Customer(
            code="CUST-1003",
            name="Vertex Manufacturing Inc.",
            segment="industrial",
            email_domain="vertexmfg.example",
            ar_contact_name="Priya Raman",
            ar_contact_email="ap@vertexmfg.example",
            payment_terms="NET30",
            credit_limit=D(500_000),
        ),
        Customer(
            code="CUST-1004",
            name="Harborline Foods Co.",
            segment="foodservice",
            email_domain="harborlinefoods.example",
            ar_contact_name="Toni Alvarez",
            ar_contact_email="remittance@harborlinefoods.example",
            payment_terms="NET30",
            credit_limit=D(120_000),
        ),
    ]
    session.add_all(rows)
    session.flush()
    return {c.code: c for c in rows}


def seed_contracts(session: Session, customers: dict[str, Customer]) -> dict[str, Contract]:
    """Mirror the contract PDFs into the ERP so SQL and RAG agree."""
    contracts: dict[str, Contract] = {}
    for doc in load_sources():
        customer = customers[doc.meta["customer_code"]]
        contract = Contract(
            customer_id=customer.id,
            contract_number=doc.contract_number,
            title=doc.meta.get("title", "Agreement"),
            effective_date=dt.date.fromisoformat(doc.meta["effective_date"]),
            expiry_date=dt.date.fromisoformat(doc.meta["expiry_date"]),
            document_filename=doc.pdf_name,
            claim_notice_days=int(doc.meta.get("claim_notice_days", 10)),
            summary=(
                f"{doc.meta.get('title')} between Acme Supply Co. and "
                f"{doc.meta.get('customer_name')}, governed by the laws of the "
                f"{doc.meta.get('governing_law', 'State of Illinois')}."
            ),
        )
        session.add(contract)
        contracts[doc.contract_number] = contract
    session.flush()
    return contracts


def seed_discounts(
    session: Session,
    customers: dict[str, Customer],
    contracts: dict[str, Contract],
    today: dt.date,
) -> None:
    session.add_all(
        [
            DiscountAgreement(
                customer_id=customers["CUST-1002"].id,
                contract_id=contracts["TC-2022-031"].id,
                code="EPD-2PCT",
                description="2% early payment discount on cleared funds within 10 days",
                discount_type="early_payment",
                rate_pct=2.0,
                pay_within_days=10,
                valid_from=today - dt.timedelta(days=400),
                valid_to=today + dt.timedelta(days=330),
                is_active=True,
                conditions=(
                    "Earned only where cleared funds for the full invoice are received "
                    "on or before day 10 from the invoice date. Measured on receipt of "
                    "cleared funds, not on the payment file date."
                ),
            ),
            DiscountAgreement(
                customer_id=customers["CUST-1001"].id,
                contract_id=contracts["MSA-2023-014"].id,
                code="VOL-Q3",
                description="3% quarterly volume rebate above $150k net purchases",
                discount_type="volume",
                rate_pct=3.0,
                pay_within_days=None,
                valid_from=today - dt.timedelta(days=200),
                valid_to=today + dt.timedelta(days=165),
                is_active=True,
                conditions=(
                    "Accrues quarterly and is settled by credit memorandum after the "
                    "quarter closes. May not be taken as a deduction from a current "
                    "invoice."
                ),
            ),
            DiscountAgreement(
                customer_id=customers["CUST-1001"].id,
                contract_id=contracts["MSA-2023-014"].id,
                code="PROMO-SPRING",
                description="5% spring promotional allowance on display fixtures",
                discount_type="promotional",
                rate_pct=5.0,
                pay_within_days=None,
                valid_from=today - dt.timedelta(days=500),
                valid_to=today - dt.timedelta(days=320),
                is_active=False,
                conditions="Expired. Applied at the point of order, never as a deduction.",
            ),
            DiscountAgreement(
                customer_id=customers["CUST-1003"].id,
                contract_id=contracts["SA-2024-007"].id,
                code="RBT-VX",
                description="2.5% quarterly volume rebate on bushing families",
                discount_type="volume",
                rate_pct=2.5,
                pay_within_days=None,
                valid_from=today - dt.timedelta(days=300),
                valid_to=today + dt.timedelta(days=65),
                is_active=True,
                conditions="Settled by credit memorandum after the quarter closes.",
            ),
        ]
    )
    session.flush()


def _invoice(
    session: Session,
    *,
    customer: Customer,
    number: str,
    po: str,
    issue_offset: int,
    due_offset: int,
    lines: list[tuple[str, str, int, str]],
    amount_paid: str,
    status: str,
    today: dt.date,
) -> Invoice:
    subtotal = sum(D(unit) * qty for _, _, qty, unit in lines)
    invoice = Invoice(
        customer_id=customer.id,
        invoice_number=number,
        po_number=po,
        issue_date=today - dt.timedelta(days=issue_offset),
        due_date=today + dt.timedelta(days=due_offset),
        currency="USD",
        subtotal=D(subtotal),
        tax_amount=D(0),
        total_amount=D(subtotal),
        amount_paid=D(amount_paid),
        status=status,
    )
    session.add(invoice)
    session.flush()
    for index, (sku, description, qty, unit) in enumerate(lines, start=1):
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_no=index,
                sku=sku,
                description=description,
                quantity=qty,
                unit_price=D(unit),
                line_total=D(D(unit) * qty),
            )
        )
    session.flush()
    return invoice


def seed_transactions(
    session: Session, customers: dict[str, Customer], today: dt.date
) -> None:
    northwind = customers["CUST-1001"]
    cascade = customers["CUST-1002"]
    vertex = customers["CUST-1003"]
    harborline = customers["CUST-1004"]

    # --- Scenario A: damaged goods, supported by the POD (valid claim) -----
    inv_a = _invoice(
        session,
        customer=northwind,
        number="INV-2025-0148",
        po="NW-88421",
        issue_offset=12,
        due_offset=18,
        lines=[("AC-1100", "Stackable poly crate, 60 L, grey", 400, "46.00")],
        amount_paid="17159.50",
        status="short_paid",
        today=today,
    )
    ship_a = Shipment(
        invoice_id=inv_a.id,
        customer_id=northwind.id,
        shipment_number="SHP-77310",
        carrier="Midwest Freight Lines",
        tracking_number="MFL8841203",
        ship_date=today - dt.timedelta(days=9),
        delivery_date=today - dt.timedelta(days=6),
        pod_signed_by="D. Whitfield",
        condition_on_delivery="damage_noted",
        exception_notes=(
            "Pallet 3 of 8 crushed in transit; 27 crates cracked and unsellable. "
            "Driver signed carrier exception CX-4471 at the dock."
        ),
    )
    session.add(ship_a)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_a.id,
            sku="AC-1100",
            quantity_ordered=400,
            quantity_shipped=400,
            quantity_damaged=27,
            unit_price=D("46.00"),
        )
    )
    session.add(
        Payment(
            customer_id=northwind.id,
            invoice_id=inv_a.id,
            payment_reference="PAY-40921",
            payment_date=today - dt.timedelta(days=2),
            amount=D("17159.50"),
            method="ACH",
            deduction_amount=D("1240.50"),
            deduction_code="DMG",
            remittance_note="Short pay: damaged crates per carrier exception CX-4471.",
        )
    )

    # --- Scenario B: early payment discount taken late (invalid claim) -----
    inv_b = _invoice(
        session,
        customer=cascade,
        number="INV-2025-0152",
        po="CH-55120",
        issue_offset=26,
        due_offset=19,
        lines=[("MD-2044", "Nitrile exam gloves, case of 10 boxes, size M", 600, "51.00")],
        amount_paid="29988.00",
        status="short_paid",
        today=today,
    )
    ship_b = Shipment(
        invoice_id=inv_b.id,
        customer_id=cascade.id,
        shipment_number="SHP-77455",
        carrier="Pacific Regional Carriers",
        tracking_number="PRC5510992",
        ship_date=today - dt.timedelta(days=24),
        delivery_date=today - dt.timedelta(days=22),
        pod_signed_by="M. Ortiz",
        condition_on_delivery="clean",
        exception_notes="",
    )
    session.add(ship_b)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_b.id,
            sku="MD-2044",
            quantity_ordered=600,
            quantity_shipped=600,
            quantity_damaged=0,
            unit_price=D("51.00"),
        )
    )
    session.add(
        Payment(
            customer_id=cascade.id,
            invoice_id=inv_b.id,
            payment_reference="PAY-41055",
            payment_date=today - dt.timedelta(days=1),
            amount=D("29988.00"),
            method="ACH",
            deduction_amount=D("612.00"),
            deduction_code="EPD",
            remittance_note="2% early payment discount taken per our standard terms.",
        )
    )

    # --- Scenario C: short shipment proven by our own log (valid claim) ----
    inv_c = _invoice(
        session,
        customer=vertex,
        number="INV-2025-0161",
        po="VX-30288",
        issue_offset=8,
        due_offset=22,
        lines=[("IND-7702", "Hardened steel bushing, 2 in, case-hardened", 500, "82.00")],
        amount_paid="38868.00",
        status="short_paid",
        today=today,
    )
    ship_c = Shipment(
        invoice_id=inv_c.id,
        customer_id=vertex.id,
        shipment_number="SHP-77502",
        carrier="Keystone Freight",
        tracking_number="KF2299104",
        ship_date=today - dt.timedelta(days=6),
        delivery_date=today - dt.timedelta(days=4),
        pod_signed_by="R. Vance",
        condition_on_delivery="short_delivered",
        exception_notes=(
            "Six of seven pallets delivered; pallet 7 (26 bushings) was not loaded at "
            "origin per the carrier manifest."
        ),
    )
    session.add(ship_c)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_c.id,
            sku="IND-7702",
            quantity_ordered=500,
            quantity_shipped=474,
            quantity_damaged=0,
            unit_price=D("82.00"),
        )
    )
    session.add(
        Payment(
            customer_id=vertex.id,
            invoice_id=inv_c.id,
            payment_reference="PAY-41102",
            payment_date=today - dt.timedelta(days=1),
            amount=D("38868.00"),
            method="ACH",
            deduction_amount=D("2132.00"),
            deduction_code="SHT",
            remittance_note="Short pay: 26 bushings not received on PO VX-30288.",
        )
    )

    # --- Scenario D: alleged duplicate that is not one (invalid claim) -----
    inv_d = _invoice(
        session,
        customer=northwind,
        number="INV-2025-0170",
        po="NW-89004",
        issue_offset=18,
        due_offset=12,
        lines=[("AC-2250", "Retail shelf divider kit, 12-pack", 250, "39.00")],
        amount_paid="6270.00",
        status="short_paid",
        today=today,
    )
    ship_d = Shipment(
        invoice_id=inv_d.id,
        customer_id=northwind.id,
        shipment_number="SHP-77520",
        carrier="Midwest Freight Lines",
        tracking_number="MFL8841377",
        ship_date=today - dt.timedelta(days=16),
        delivery_date=today - dt.timedelta(days=13),
        pod_signed_by="D. Whitfield",
        condition_on_delivery="clean",
        exception_notes="",
    )
    session.add(ship_d)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_d.id,
            sku="AC-2250",
            quantity_ordered=250,
            quantity_shipped=250,
            quantity_damaged=0,
            unit_price=D("39.00"),
        )
    )
    session.add(
        Payment(
            customer_id=northwind.id,
            invoice_id=inv_d.id,
            payment_reference="PAY-41140",
            payment_date=today - dt.timedelta(days=2),
            amount=D("6270.00"),
            method="ACH",
            deduction_amount=D("3480.00"),
            deduction_code="DUP",
            remittance_note="Deducted as a duplicate of INV-2025-0143.",
        )
    )

    # The invoice the customer mistakes for a duplicate: different PO, different
    # goods, already settled in full.
    inv_e = _invoice(
        session,
        customer=northwind,
        number="INV-2025-0143",
        po="NW-88320",
        issue_offset=60,
        due_offset=-30,
        lines=[("AC-3300", "Endcap display frame, powder-coated", 120, "29.00")],
        amount_paid="3480.00",
        status="paid",
        today=today,
    )
    ship_e = Shipment(
        invoice_id=inv_e.id,
        customer_id=northwind.id,
        shipment_number="SHP-77120",
        carrier="Midwest Freight Lines",
        tracking_number="MFL8840551",
        ship_date=today - dt.timedelta(days=58),
        delivery_date=today - dt.timedelta(days=55),
        pod_signed_by="D. Whitfield",
        condition_on_delivery="clean",
        exception_notes="",
    )
    session.add(ship_e)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_e.id,
            sku="AC-3300",
            quantity_ordered=120,
            quantity_shipped=120,
            quantity_damaged=0,
            unit_price=D("29.00"),
        )
    )
    session.add(
        Payment(
            customer_id=northwind.id,
            invoice_id=inv_e.id,
            payment_reference="PAY-40410",
            payment_date=today - dt.timedelta(days=33),
            amount=D("3480.00"),
            method="ACH",
            deduction_amount=D("0"),
            deduction_code=None,
            remittance_note="Paid in full.",
        )
    )

    # --- A clean, fully paid invoice so triage has a non-dispute to reject -
    inv_f = _invoice(
        session,
        customer=harborline,
        number="INV-2025-0181",
        po="HL-12007",
        issue_offset=40,
        due_offset=-10,
        lines=[("FD-5510", "Kraft takeout box, 200 per case", 200, "27.10")],
        amount_paid="5420.00",
        status="paid",
        today=today,
    )
    ship_f = Shipment(
        invoice_id=inv_f.id,
        customer_id=harborline.id,
        shipment_number="SHP-77600",
        carrier="Coastal Parcel",
        tracking_number="CP7781200",
        ship_date=today - dt.timedelta(days=38),
        delivery_date=today - dt.timedelta(days=36),
        pod_signed_by="T. Alvarez",
        condition_on_delivery="clean",
        exception_notes="",
    )
    session.add(ship_f)
    session.flush()
    session.add(
        ShipmentLine(
            shipment_id=ship_f.id,
            sku="FD-5510",
            quantity_ordered=200,
            quantity_shipped=200,
            quantity_damaged=0,
            unit_price=D("27.10"),
        )
    )
    session.add(
        Payment(
            customer_id=harborline.id,
            invoice_id=inv_f.id,
            payment_reference="PAY-41010",
            payment_date=today - dt.timedelta(days=9),
            amount=D("5420.00"),
            method="ACH",
            deduction_amount=D("0"),
            deduction_code=None,
            remittance_note="Paid in full, no adjustments.",
        )
    )
    session.flush()


def seed_all(reset: bool = True, with_inbox: bool = True) -> dict[str, object]:
    """Create the schema, load fixtures, index the contract PDFs, fill the inbox."""
    today = dt.date.today()
    init_db(drop=reset)

    index_info = build_contract_index(reset=reset)

    with session_scope() as session:
        if session.scalar(select(Customer).limit(1)) is None:
            customers = seed_customers(session, today)
            contracts = seed_contracts(session, customers)
            seed_discounts(session, customers, contracts, today)
            seed_transactions(session, customers, today)
        else:
            customers = {
                c.code: c for c in session.scalars(select(Customer)).all()
            }
        inbox = load_samples(session) if with_inbox else []
        return {
            "customers": len(customers),
            "invoices": len(session.scalars(select(Invoice)).all()),
            "inbox_messages": len(inbox),
            **index_info,
        }
