"""The confirm endpoint is the only way onto the ledger."""

from __future__ import annotations

from decimal import Decimal


def _invoice(client, number: str) -> dict:
    invoices = client.get("/api/invoices").json()
    return next(item for item in invoices if item["invoice_number"] == number)


def _open(client, number: str) -> str:
    return _invoice(client, number)["open_amount"]


def test_processing_does_not_post(client):
    outcomes = client.get("/api/outcomes").json()
    assert outcomes["distinct"] is True
    kinds = [row["kind"] for row in outcomes["outcomes"]]
    assert kinds == ["full", "full", "short_pay", "overpay", "unapplied"]
    for ref in ("LBX-20260918-014", "EML-20260919-CASCADE", "LBX-20260918-102"):
        body = client.get(f"/api/remittances/{ref}").json()
        assert body["receipt"] is None
        assert body["status"] != "posted"
    assert _open(client, "INV-10481") == "4250.00"
    assert _invoice(client, "INV-10481")["status"] == "open"
    assert _open(client, "INV-10571") == "7333.33"


def test_apply_exact_match_posts_and_cannot_post_twice(client):
    response = client.post(
        "/api/remittances/LBX-20260918-014/confirm",
        json={"action": "apply", "clerk": "Alex Chen"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "posted"
    assert body["receipt"]["status"] == "applied"
    assert body["receipt"]["applied_amount"] == "4250.00"
    assert body["receipt"]["unapplied_amount"] == "0.00"
    assert body["receipt"]["posted_by"] == "Alex Chen"
    assert body["receipt"]["applications"][0]["open_after"] == "0.00"
    assert _invoice(client, "INV-10481")["status"] == "paid"
    assert _open(client, "INV-10481") == "0.00"

    again = client.post(
        "/api/remittances/LBX-20260918-014/confirm",
        json={"action": "apply", "clerk": "Alex Chen"},
    )
    assert again.status_code == 409


def test_short_pay_confirm_leaves_the_invoice_open(client):
    response = client.post(
        "/api/remittances/EML-20260919-CASCADE/confirm",
        json={"action": "apply", "clerk": "Priya Shah"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["receipt"]["applied_amount"] == "11240.00"
    assert body["receipt"]["unapplied_amount"] == "0.00"
    assert _invoice(client, "INV-10510")["status"] == "partial"
    assert _open(client, "INV-10510") == "1240.00"
    assert _open(client, "INV-10522") == "3260.75"


def test_overpay_confirm_keeps_the_remainder_unapplied(client):
    response = client.post(
        "/api/remittances/LBX-20260918-088/confirm",
        json={"action": "apply", "clerk": "Alex Chen"},
    )
    assert response.status_code == 200
    receipt = response.json()["receipt"]
    assert receipt["status"] == "partially_applied"
    assert receipt["applied_amount"] == "5600.00"
    assert receipt["unapplied_amount"] == "500.00"
    assert _invoice(client, "INV-10550")["status"] == "paid"
    assert _open(client, "INV-10558") == "2225.40"


def test_leave_unapplied_does_not_touch_invoices(client):
    before = client.get("/api/invoices").json()
    response = client.post(
        "/api/remittances/LBX-20260918-102/confirm",
        json={"action": "leave_unapplied", "clerk": "Alex Chen"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["receipt"]["status"] == "unapplied"
    assert body["receipt"]["applied_amount"] == "0.00"
    assert body["receipt"]["unapplied_amount"] == "2000.00"
    assert body["receipt"]["applications"] == []
    assert client.get("/api/invoices").json() == before


def test_apply_is_rejected_for_unapplied_cash(client):
    response = client.post(
        "/api/remittances/LBX-20260918-102/confirm",
        json={"action": "apply", "clerk": "Alex Chen"},
    )
    assert response.status_code == 422
    assert client.get("/api/remittances/LBX-20260918-102").json()["receipt"] is None


def test_split_can_place_an_overpay_on_a_second_invoice(client):
    response = client.post(
        "/api/remittances/LBX-20260918-088/confirm",
        json={
            "action": "split",
            "clerk": "Alex Chen",
            "lines": [
                {"invoice_number": "INV-10550", "amount": "5600.00"},
                {"invoice_number": "INV-10558", "amount": "500.00"},
            ],
        },
    )
    assert response.status_code == 200
    receipt = response.json()["receipt"]
    assert receipt["status"] == "applied"
    assert receipt["unapplied_amount"] == "0.00"
    assert _open(client, "INV-10550") == "0.00"
    assert _open(client, "INV-10558") == "1725.40"


def test_split_cannot_exceed_the_payment_or_the_open_balance(client):
    over = client.post(
        "/api/remittances/LBX-20260918-088/confirm",
        json={
            "action": "split",
            "clerk": "Alex Chen",
            "lines": [{"invoice_number": "INV-10550", "amount": "6200.00"}],
        },
    )
    assert over.status_code == 422
    too_open = client.post(
        "/api/remittances/LBX-20260918-088/confirm",
        json={
            "action": "split",
            "clerk": "Alex Chen",
            "lines": [{"invoice_number": "INV-10550", "amount": "5700.00"}],
        },
    )
    assert too_open.status_code == 409
    assert _open(client, "INV-10550") == "5600.00"
    assert _open(client, "INV-10558") == "2225.40"


def test_clerk_name_is_required(client):
    response = client.post(
        "/api/remittances/LBX-20260918-014/confirm",
        json={"action": "apply", "clerk": " "},
    )
    assert response.status_code == 422
    assert _open(client, "INV-10481") == "4250.00"


def test_multi_invoice_apply_clears_both(client):
    response = client.post(
        "/api/remittances/EDI-820-20260918-VM/confirm",
        json={"action": "apply", "clerk": "Alex Chen"},
    )
    assert response.status_code == 200
    assert response.json()["receipt"]["applied_amount"] == "11050.00"
    assert _open(client, "INV-10530") == "0.00"
    assert _open(client, "INV-10531") == "0.00"
    assert _invoice(client, "INV-10544")["status"] == "paid"
    assert _open(client, "INV-10544") == "0.00"


def test_queue_page_shows_each_outcome_and_one_click_posts(client):
    page = client.get("/")
    assert page.status_code == 200
    for label in ("Full", "Short pay", "Overpay", "Unapplied"):
        assert label in page.text
    assert "LBX-20260918-014" in page.text
    assert "EML-20260919-CASCADE" in page.text
    posted = client.post(
        "/remittances/LBX-20260918-014/confirm",
        data={"action": "apply", "clerk": "Alex Chen"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    detail = client.get("/remittances/LBX-20260918-014")
    assert "Posted by Alex Chen" in detail.text
    assert _open(client, "INV-10481") == "0.00"


def test_new_remittance_is_not_posted_until_confirm(client):
    created = client.post(
        "/api/remittances",
        json={
            "external_ref": "LBX-NEW-1",
            "channel": "lockbox",
            "raw_text": "\n".join(
                [
                    "LOCKBOX REMITTANCE ADVICE",
                    "Payer: Cascade Health Systems",
                    "Account: CH-22018",
                    "Check number: 77",
                    "Check amount: 3260.75",
                    "Invoices: INV-10522",
                    "PO: CH-PO-4502",
                ]
            ),
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "full"
    assert body["queue"] == "ready"
    assert body["receipt"] is None
    assert _open(client, "INV-10522") == "3260.75"
    assert Decimal(body["confidence"]) >= Decimal("0.90")
