"""A clerk confirms the route. The confirm endpoint does not send anything."""

from deduction_workbench.cli import main
from deduction_workbench.db import connect, sent_count


def test_confirm_endpoint_records_the_route_and_does_not_send(client) -> None:
    before = client.get("/api/cases/DED-1001").json()
    assert before["status"] == "awaiting_confirmation"
    assert before["queue"] == "warehouse"

    response = client.post(
        "/api/cases/DED-1001/confirm",
        json={"clerk_id": "clerk", "note": "POD is short. Warehouse can research it."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["queue"] == "warehouse"
    assert body["confirmed_by"] == "clerk"
    assert body["sent"] is False
    assert body["auto_send"] is False
    assert body["dispatched"] is False
    assert body["confirmation_note"] == "POD is short. Warehouse can research it."

    page = client.get("/cases/DED-1001")
    assert page.status_code == 200
    assert "Nothing was sent" in page.text
    assert "Warehouse" in page.text

    again = client.post(
        "/api/cases/DED-1001/confirm",
        json={"clerk_id": "clerk", "note": "second look"},
    )
    assert again.status_code == 409

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["sent"] == 0
    assert health["llm_provider"] == "offline"
    assert health["confirmed"] == 1


def test_confirm_requires_a_clerk_and_a_real_case(client) -> None:
    missing = client.post("/api/cases/DED-1001/confirm", json={"note": "no clerk"})
    assert missing.status_code == 422
    blank = client.post("/api/cases/DED-1001/confirm", json={"clerk_id": "   "})
    assert blank.status_code == 422
    unknown = client.post("/api/cases/DED-9999/confirm", json={"clerk_id": "clerk"})
    assert unknown.status_code == 404
    bad_queue = client.post(
        "/api/cases/DED-1005/confirm",
        json={"clerk_id": "clerk", "queue": "customer"},
    )
    assert bad_queue.status_code == 422
    still = client.get("/api/cases/DED-1005").json()
    assert still["status"] == "awaiting_confirmation"


def test_clerk_can_redirect_without_sending(client) -> None:
    response = client.post(
        "/api/cases/DED-1009/confirm",
        json={"clerk_id": "clerk", "queue": "recovery", "note": "Hold for a second look."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queue"] == "recovery"
    assert body["proposed_queue"] == "quality"
    assert body["overridden"] is True
    assert body["sent"] is False
    assert body["dispatched"] is False


def test_form_confirm_redirects_and_holds_the_deduction(client) -> None:
    response = client.post(
        "/cases/DED-1003/confirm",
        data={"clerk_id": "clerk", "note": "Promo rate matches.", "queue": "sales"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/cases/DED-1003?confirmed=1")
    page = client.get("/cases/DED-1003")
    assert "Nothing was sent" in page.text
    assert "Sales" in page.text
    detail = client.get("/api/cases/DED-1003").json()
    assert detail["status"] == "confirmed"
    assert detail["queue"] == "sales"
    assert detail["sent"] is False


def test_queue_page_lists_every_desk(client) -> None:
    page = client.get("/")
    assert page.status_code == 200
    text = page.text
    assert "Deduction Workbench" in text
    for label in ("Warehouse", "Sales", "Sales co-op", "Returns", "Quality", "Recovery"):
        assert label in text
    assert "DED-1001" in text
    assert "sent" in text.lower()
    missing = client.get("/cases/DED-0000")
    assert missing.status_code == 404


def test_cli_confirm_and_routes(app, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEDUCTION_DB_PATH", app.state.db_path)
    monkeypatch.setenv("DEDUCTION_LLM_PROVIDER", "offline")
    assert main(["routes"]) == 0
    routes = capsys.readouterr().out
    assert "Each reason code routes differently: yes" in routes
    assert "shortage -> warehouse" in routes
    assert "pricing -> sales" in routes
    assert "coop_advertising -> sales_coop" in routes
    assert "returns -> returns" in routes
    assert "damaged_goods -> quality" in routes

    assert main(["confirm", "DED-1006", "--clerk", "clerk", "--note", "No RMA on file."]) == 0
    confirmed = capsys.readouterr().out
    assert "Nothing was sent." in confirmed
    assert "recovery" in confirmed

    conn = connect(app.state.db_path)
    try:
        assert sent_count(conn) == 0
    finally:
        conn.close()

    assert main(["confirm", "DED-1006", "--clerk", "clerk"]) == 1
    assert "already confirmed" in capsys.readouterr().out
