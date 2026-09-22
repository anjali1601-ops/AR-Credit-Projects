"""The emails the simulated inbox starts with.

Two claims are genuinely supported by the ERP records, two are not, and one is
not a dispute at all so the triage step has something to reject.
"""

from __future__ import annotations

SAMPLE_EMAILS: list[dict[str, object]] = [
    {
        "message_id": "msg-northwind-damage-0148",
        "sender": "ap@northwind-retail.example",
        "sender_name": "Dana Whitfield",
        "subject": "Short payment on INV-2025-0148 - damaged crates",
        "hours_ago": 7,
        "expected": "valid",
        "body": """Hi Acme AR team,

We processed payment for invoice INV-2025-0148 today but short-paid it by $1,240.50.

Pallet 3 arrived crushed and 27 of the poly crates were cracked and unsellable. Our
receiver noted the damage on the delivery receipt and the driver signed carrier
exception CX-4471 at the dock. Photos are on file if you need them.

Please issue a credit memo for the $1,240.50 deduction against the $18,400.00 invoice.
Our PO NW-88421 refers.

Thanks,
Dana Whitfield
Accounts Payable, Northwind Retail Group""",
    },
    {
        "message_id": "msg-cascade-discount-0152",
        "sender": "payables@cascadehealth.example",
        "sender_name": "Marcus Bell",
        "subject": "Remittance for INV-2025-0152",
        "hours_ago": 5,
        "expected": "invalid",
        "body": """Hello,

Payment went out today for invoice INV-2025-0152. We remitted $29,988.00 and deducted
$612.00 as our standard 2% early payment discount, per the discount schedule on our
account. PO CH-55120.

Let us know if anything looks off on your side.

Marcus Bell
Payables, Cascade Health Supply""",
    },
    {
        "message_id": "msg-vertex-shortage-0161",
        "sender": "ap@vertexmfg.example",
        "sender_name": "Priya Raman",
        "subject": "INV-2025-0161 short shipment - 26 bushings not received",
        "hours_ago": 3,
        "expected": "valid",
        "body": """Acme AR,

We are short-paying invoice INV-2025-0161 by $2,132.00. Receiving counted 474 of the 500
hardened steel bushings against PO VX-30288 - pallet 7 never showed up, and your driver's
manifest only lists six pallets.

Please credit the shortage so we can close the invoice out.

Priya Raman
Accounts Payable, Vertex Manufacturing""",
    },
    {
        "message_id": "msg-northwind-duplicate-0170",
        "sender": "ap@northwind-retail.example",
        "sender_name": "Dana Whitfield",
        "subject": "Duplicate billing - INV-2025-0170",
        "hours_ago": 2,
        "expected": "invalid",
        "body": """Hi,

We believe invoice INV-2025-0170 duplicates invoice INV-2025-0143, which we already paid
two months ago. We have deducted $3,480.00 from this month's payment run and remitted the
balance of $6,270.00.

Please confirm the duplicate has been cancelled on your side.

Dana Whitfield
Accounts Payable, Northwind Retail Group""",
    },
    {
        "message_id": "msg-harborline-remittance-0181",
        "sender": "remittance@harborlinefoods.example",
        "sender_name": "Toni Alvarez",
        "subject": "Remittance advice - INV-2025-0181",
        "hours_ago": 1,
        "expected": "skipped",
        "body": """Hello Acme,

Remittance advice attached for invoice INV-2025-0181, paid in full at $5,420.00 by ACH
this morning. No adjustments were taken.

Toni Alvarez
Harborline Foods Co.""",
    },
]
