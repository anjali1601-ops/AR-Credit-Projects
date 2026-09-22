"""Approve endpoint: gated actions do not post until a named credit manager acts."""

def _reviews_by_account(client):
    body = client.post("/reviews/run").json()
    return {review["account_id"]: review for review in body["reviews"]}


def test_health_and_desk(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "Credit Portfolio Surveillance" in page.text


def test_api_runs_all_four_outcomes(client):
    reviews = _reviews_by_account(client)
    assert reviews["NW-1044"]["action"] == "affirm"
    assert reviews["HB-2201"]["action"] == "reduce"
    assert reviews["HB-2201"]["proposed_limit"] == "112000.00"
    assert reviews["VP-3310"]["action"] == "conditions"
    assert reviews["RL-4408"]["action"] == "suspend"
    assert reviews["RL-4408"]["status"] == "pending_approval"
    for review in reviews.values():
        lines = set(review["narrative"].splitlines())
        for key, value in review["cited_figures"].items():
            assert f"{key}={value}" in lines


def test_within_authority_post_updates_the_account(client):
    reviews = _reviews_by_account(client)
    harbor = reviews["HB-2201"]
    before = client.get("/accounts/HB-2201").json()
    assert before["credit_limit"] == "150000.00"

    posted = client.post(
        f"/reviews/{harbor['id']}/post",
        json={"actor_name": "Alex Kim", "actor_role": "analyst"},
    )
    assert posted.status_code == 200
    body = posted.json()
    assert body["status"] == "posted"
    assert body["posted_by"] == "Alex Kim"
    assert body["approver_name"] is None
    assert client.get("/accounts/HB-2201").json()["credit_limit"] == "112000.00"

    northwind = reviews["NW-1044"]
    affirm = client.post(
        f"/reviews/{northwind['id']}/post",
        json={"actor_name": "Alex Kim", "actor_role": "analyst"},
    )
    assert affirm.status_code == 200
    assert client.get("/accounts/NW-1044").json()["credit_limit"] == "100000.00"

    vesper = reviews["VP-3310"]
    conditions = client.post(
        f"/reviews/{vesper['id']}/post",
        json={"actor_name": "Alex Kim", "actor_role": "analyst"},
    )
    assert conditions.status_code == 200
    account = client.get("/accounts/VP-3310").json()
    assert account["credit_limit"] == "80000.00"
    assert account["status"] == "open"
    assert account["conditions"]
    assert any("14000.00" in line for line in account["conditions"])


def test_suspension_cannot_post_until_a_named_credit_manager_approves(client):
    reviews = _reviews_by_account(client)
    redline = reviews["RL-4408"]
    assert client.get("/accounts/RL-4408").json()["status"] == "open"

    blocked = client.post(
        f"/reviews/{redline['id']}/post",
        json={"actor_name": "Alex Kim", "actor_role": "analyst"},
    )
    assert blocked.status_code == 403
    assert "credit manager" in blocked.json()["detail"].lower()
    assert client.get("/accounts/RL-4408").json()["status"] == "open"

    analyst = client.post(
        f"/reviews/{redline['id']}/approve",
        json={"approver_name": "Alex Kim", "approver_role": "analyst"},
    )
    assert analyst.status_code == 403

    blank = client.post(
        f"/reviews/{redline['id']}/approve",
        json={"approver_name": "   ", "approver_role": "credit_manager"},
    )
    assert blank.status_code == 422

    approved = client.post(
        f"/reviews/{redline['id']}/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "posted"
    assert body["approver_name"] == "Jordan Hale"
    assert body["approver_role"] == "credit_manager"
    assert client.get("/accounts/RL-4408").json()["status"] == "suspended"

    again = client.post(
        f"/reviews/{redline['id']}/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    assert again.status_code == 409


def test_stale_pending_review_cannot_be_approved_after_a_rerun(client):
    first = _reviews_by_account(client)["RL-4408"]
    second = _reviews_by_account(client)["RL-4408"]
    assert first["id"] != second["id"]
    stale = client.post(
        f"/reviews/{first['id']}/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    assert stale.status_code == 409
    assert client.get("/accounts/RL-4408").json()["status"] == "open"


def test_limit_increase_approve_endpoint(client):
    requested = client.post(
        "/accounts/NW-1044/limit-request",
        json={"requested_limit": "120000.00"},
    )
    assert requested.status_code == 200
    review = client.post("/reviews/run", params={"account_id": "NW-1044"}).json()["reviews"][0]
    assert review["action"] == "increase"
    assert review["proposed_limit"] == "120000.00"
    assert review["status"] == "pending_approval"
    assert review["authority"]["code"] == "limit_increase"

    blocked = client.post(
        f"/reviews/{review['id']}/post",
        json={"actor_name": "Alex Kim", "actor_role": "analyst"},
    )
    assert blocked.status_code == 403
    assert client.get("/accounts/NW-1044").json()["credit_limit"] == "100000.00"

    approved = client.post(
        f"/reviews/{review['id']}/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    assert approved.status_code == 200
    assert client.get("/accounts/NW-1044").json()["credit_limit"] == "120000.00"


def test_limit_request_on_redline_still_suspends(client):
    client.post("/accounts/RL-4408/limit-request", json={"requested_limit": "250000.00"})
    review = client.post("/reviews/run", params={"account_id": "RL-4408"}).json()["reviews"][0]
    assert review["action"] == "suspend"
    assert review["proposed_limit"] == "200000.00"


def test_seed_resets_a_posted_suspension(client):
    review = _reviews_by_account(client)["RL-4408"]
    client.post(
        f"/reviews/{review['id']}/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    seeded = client.post("/portfolio/seed")
    assert seeded.status_code == 200
    assert seeded.json()["account_ids"] == ["NW-1044", "HB-2201", "VP-3310", "RL-4408"]
    assert client.get("/accounts/RL-4408").json()["status"] == "open"
    assert client.get("/portfolio").json()["total_exposure"] == "454000.00"


def test_unknown_review_is_404(client):
    missing = client.post(
        "/reviews/RV-missing/approve",
        json={"approver_name": "Jordan Hale", "approver_role": "credit_manager"},
    )
    assert missing.status_code == 404
