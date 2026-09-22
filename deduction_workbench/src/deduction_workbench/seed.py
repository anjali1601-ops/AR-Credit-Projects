"""Seeded deduction backups. One case for each reason code, valid and invalid.

The classifier sees only the email and the debit memo. The policy agent sees
the facts, which stand in for the ERP and the agreement master data. Expected
outcomes are not stored here.
"""

from __future__ import annotations

NORTHLINE = ["MSA-2024-118", "PROMO-2026-Q1", "COOP-2025-NL"]
PINNACLE = ["MSA-2025-077"]

SEED_CASES: list[dict] = [
    {
        "id": "DED-1001",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-88421",
        "invoice_number": "INV-44190",
        "claimed_amount": 1860.00,
        "claim_date": "2026-03-09",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Debit DM-88421 shortage on INV-44190\n\n"
            "Northline Grocery accounts payable is deducting invoice INV-44190. "
            "The carrier POD for PO NL-77821 shows we received 138 cases of "
            "HF-2204 Brown Rice Crisps. You invoiced 200 cases. We are deducting "
            "the 62 missing cases at the invoice price, $1,860.00, on debit memo "
            "DM-88421 dated March 9, 2026. Please have the warehouse research the "
            "short shipment."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-88421\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-03-09\n"
            "Invoice: INV-44190\n"
            "PO: NL-77821\n"
            "Reason: Shortage\n"
            "SKU: HF-2204\n"
            "Comment: short shipment, POD below invoice quantity\n"
            "Deduction: $1,860.00"
        ),
        "facts": {
            "sku": "HF-2204",
            "unit_price": 30.00,
            "invoice_date": "2026-02-20",
            "delivery_date": "2026-03-02",
            "invoiced_qty": 200,
            "pod_qty": 138,
        },
    },
    {
        "id": "DED-1002",
        "customer_id": "pinnacle",
        "customer_name": "Pinnacle Club Wholesale",
        "debit_memo": "DM-22018",
        "invoice_number": "INV-55012",
        "claimed_amount": 960.00,
        "claim_date": "2026-03-06",
        "agreement_ids": PINNACLE,
        "backup_email": (
            "From: deductions@pinnacleclub.example\n"
            "Subject: Shortage deduction DM-22018\n\n"
            "Pinnacle Club Wholesale is taking a shortage deduction of $960.00 "
            "on debit memo DM-22018 against invoice INV-55012. We count a "
            "shortage of 80 cases of HF-1188. The POD reference is PCL-4419. "
            "Please research the missing cases."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-22018\n"
            "Customer: Pinnacle Club Wholesale\n"
            "Date: 2026-03-06\n"
            "Invoice: INV-55012\n"
            "Reason: Shortage\n"
            "SKU: HF-1188\n"
            "Comment: customer alleges a short shipment of 80 cases\n"
            "Deduction: $960.00"
        ),
        "facts": {
            "sku": "HF-1188",
            "unit_price": 12.00,
            "invoice_date": "2026-02-18",
            "delivery_date": "2026-03-01",
            "invoiced_qty": 80,
            "pod_qty": 80,
        },
    },
    {
        "id": "DED-1003",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-88490",
        "invoice_number": "INV-44210",
        "claimed_amount": 3000.00,
        "claim_date": "2026-03-20",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Promotional bill-back DM-88490\n\n"
            "Northline Grocery is taking the promotional bill-back on the Spring "
            "Feature. SKU HF-4410 shipped March 12, 2026 under PROMO-2026-Q1. "
            "2,400 units at the authorized scan allowance of $1.25, deduction "
            "$3,000.00 on debit memo DM-88490 against invoice INV-44210."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-88490\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-03-20\n"
            "Invoice: INV-44210\n"
            "Reason: Pricing\n"
            "SKU: HF-4410\n"
            "Comment: promotional bill-back, scan allowance, PROMO-2026-Q1\n"
            "Deduction: $3,000.00"
        ),
        "facts": {
            "sku": "HF-4410",
            "unit_price": 6.40,
            "invoice_date": "2026-03-13",
            "ship_date": "2026-03-12",
            "claimed_units": 2400,
            "claimed_rate": 1.25,
            "promo_used": 2100.00,
        },
    },
    {
        "id": "DED-1004",
        "customer_id": "pinnacle",
        "customer_name": "Pinnacle Club Wholesale",
        "debit_memo": "DM-22102",
        "invoice_number": "INV-55110",
        "claimed_amount": 1000.00,
        "claim_date": "2026-03-18",
        "agreement_ids": PINNACLE,
        "backup_email": (
            "From: deductions@pinnacleclub.example\n"
            "Subject: Price difference DM-22102\n\n"
            "Pinnacle Club Wholesale deducts a price difference of $2.00 per "
            "unit on 500 units of HF-4410. The shipment left on March 12, 2026. "
            "We were quoted a promotional price. Debit memo DM-22102 for "
            "$1,000.00 against invoice INV-55110."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-22102\n"
            "Customer: Pinnacle Club Wholesale\n"
            "Date: 2026-03-18\n"
            "Invoice: INV-55110\n"
            "Reason: Pricing\n"
            "SKU: HF-4410\n"
            "Comment: promotional price difference, $2.00 per unit\n"
            "Deduction: $1,000.00"
        ),
        "facts": {
            "sku": "HF-4410",
            "unit_price": 6.40,
            "invoice_date": "2026-03-13",
            "ship_date": "2026-03-12",
            "claimed_units": 500,
            "claimed_rate": 2.00,
            "promo_used": 0,
        },
    },
    {
        "id": "DED-1005",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-87910",
        "invoice_number": "INV-43880",
        "claimed_amount": 740.00,
        "claim_date": "2026-02-02",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Return deduction DM-87910 under RMA-55219\n\n"
            "Please apply debit memo DM-87910 for $740.00. We returned 40 cases "
            "of HF-3301 under RMA-55219 against invoice INV-43880. The goods "
            "shipped back on February 2, 2026. This is an authorized return."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-87910\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-02-02\n"
            "Invoice: INV-43880\n"
            "Reason: Returns\n"
            "RMA: RMA-55219\n"
            "SKU: HF-3301\n"
            "Comment: authorized return, 40 cases sent back\n"
            "Deduction: $740.00"
        ),
        "facts": {
            "sku": "HF-3301",
            "unit_price": 18.50,
            "invoice_date": "2026-01-08",
            "rma_number": "RMA-55219",
            "rma_authorized": True,
            "rma_qty": 40,
            "return_qty": 40,
        },
    },
    {
        "id": "DED-1006",
        "customer_id": "pinnacle",
        "customer_name": "Pinnacle Club Wholesale",
        "debit_memo": "DM-21940",
        "invoice_number": "INV-54880",
        "claimed_amount": 500.00,
        "claim_date": "2026-02-01",
        "agreement_ids": PINNACLE,
        "backup_email": (
            "From: deductions@pinnacleclub.example\n"
            "Subject: Return deduction DM-21940\n\n"
            "Pinnacle Club is deducting $500.00 for product we sent back against "
            "invoice INV-54880. Debit memo DM-21940. We did not request an "
            "authorization before the return shipped. Twenty cases of HF-3301 "
            "went back to you."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-21940\n"
            "Customer: Pinnacle Club Wholesale\n"
            "Date: 2026-02-01\n"
            "Invoice: INV-54880\n"
            "Reason: Returns\n"
            "SKU: HF-3301\n"
            "Comment: unsold goods sent back, no authorization on the return\n"
            "Deduction: $500.00"
        ),
        "facts": {
            "sku": "HF-3301",
            "unit_price": 25.00,
            "invoice_date": "2026-01-15",
            "rma_number": None,
            "rma_authorized": False,
            "rma_qty": None,
            "return_qty": 20,
        },
    },
    {
        "id": "DED-1007",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-88002",
        "invoice_number": "INV-43940",
        "claimed_amount": 4200.00,
        "claim_date": "2026-02-10",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Co-op advertising deduction DM-88002\n\n"
            "Northline Grocery is deducting co-op advertising of $4,200.00 on "
            "debit memo DM-88002, referenced to invoice INV-43940. The January 18, "
            "2026 circular featured Harbor & Field. The tear sheet and the media "
            "invoice are attached as proof of performance, submitted February 10. "
            "This draws the accrual under COOP-2025-NL."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-88002\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-02-10\n"
            "Invoice: INV-43940\n"
            "Reason: Co-op advertising\n"
            "Agreement: COOP-2025-NL\n"
            "Comment: tear sheet attached, proof of performance, accrual draw\n"
            "Deduction: $4,200.00"
        ),
        "facts": {
            "sku": None,
            "invoice_date": "2026-01-20",
            "features_our_brand": True,
            "proof_submitted": True,
            "ad_run_date": "2026-01-18",
            "proof_date": "2026-02-10",
            "accrual_balance": 6100.00,
        },
    },
    {
        "id": "DED-1008",
        "customer_id": "pinnacle",
        "customer_name": "Pinnacle Club Wholesale",
        "debit_memo": "DM-21880",
        "invoice_number": "INV-54770",
        "claimed_amount": 1500.00,
        "claim_date": "2026-02-12",
        "agreement_ids": PINNACLE,
        "backup_email": (
            "From: deductions@pinnacleclub.example\n"
            "Subject: Co-op advertising DM-21880\n\n"
            "Pinnacle Club ran a newspaper advertisement featuring your brand and "
            "is deducting co-op advertising of $1,500.00 on debit memo DM-21880 "
            "against invoice INV-54770. A tear sheet is attached as proof of "
            "performance. Please draw this from the accrual."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-21880\n"
            "Customer: Pinnacle Club Wholesale\n"
            "Date: 2026-02-12\n"
            "Invoice: INV-54770\n"
            "Reason: Co-op advertising\n"
            "Comment: tear sheet attached, advertising accrual\n"
            "Deduction: $1,500.00"
        ),
        "facts": {
            "sku": None,
            "invoice_date": "2026-01-28",
            "features_our_brand": True,
            "proof_submitted": True,
            "ad_run_date": "2026-01-20",
            "proof_date": "2026-02-12",
            "accrual_balance": 0,
        },
    },
    {
        "id": "DED-1009",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-88610",
        "invoice_number": "INV-44330",
        "claimed_amount": 330.00,
        "claim_date": "2026-03-08",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Damaged goods on INV-44330\n\n"
            "Concealed damage on invoice INV-44330. Twelve cases of HF-5502 "
            "arrived crushed. The carrier exception is noted and photographs are "
            "on file. We are deducting $330.00 on debit memo DM-88610, claimed "
            "March 8, 2026. This is a damaged-goods claim."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-88610\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-03-08\n"
            "Invoice: INV-44330\n"
            "Reason: Damaged goods\n"
            "SKU: HF-5502\n"
            "Comment: crushed cases, concealed damage, photos and carrier exception\n"
            "Deduction: $330.00"
        ),
        "facts": {
            "sku": "HF-5502",
            "unit_price": 27.50,
            "invoice_date": "2026-03-01",
            "delivery_date": "2026-03-04",
            "damaged_qty": 12,
            "damage_evidence": True,
        },
    },
    {
        "id": "DED-1010",
        "customer_id": "northline",
        "customer_name": "Northline Grocery",
        "debit_memo": "DM-88150",
        "invoice_number": "INV-44002",
        "claimed_amount": 220.00,
        "claim_date": "2026-02-20",
        "agreement_ids": NORTHLINE,
        "backup_email": (
            "From: ap@northlinegrocery.example\n"
            "Subject: Damaged goods deduction DM-88150\n\n"
            "Northline is deducting $220.00 for damaged goods on invoice "
            "INV-44002, debit memo DM-88150. Eight cases of HF-5502 were crushed "
            "in transit. We are filing the claim on February 20, 2026. Photos "
            "of the concealed damage are attached."
        ),
        "debit_memo_text": (
            "DEBIT MEMO DM-88150\n"
            "Customer: Northline Grocery\n"
            "Date: 2026-02-20\n"
            "Invoice: INV-44002\n"
            "Reason: Damaged goods\n"
            "SKU: HF-5502\n"
            "Comment: crushed cases, concealed damage, photographs attached\n"
            "Deduction: $220.00"
        ),
        "facts": {
            "sku": "HF-5502",
            "unit_price": 27.50,
            "invoice_date": "2026-01-27",
            "delivery_date": "2026-02-01",
            "damaged_qty": 8,
            "damage_evidence": True,
        },
    },
]
