"""A rating cannot be posted until the manager confirms it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from team_lead.api import create_app
from team_lead.case import run_case
from team_lead.errors import RatingNotConfirmed
from team_lead.llm import STAY_MARKER


def test_post_without_confirm_sends_nothing(store):
    case = run_case(store, "TL-MAYA")
    assert case.confirmed_rating is None
    with pytest.raises(RatingNotConfirmed):
        store.post("TL-MAYA", "hr")
    saved = store.get_case("TL-MAYA")
    assert saved is not None
    assert saved.transmissions == []
    with pytest.raises(RatingNotConfirmed):
        store.post("TL-MAYA", "employee")
    assert store.get_case("TL-MAYA").transmissions == []


def test_confirm_does_not_send(store):
    run_case(store, "TL-ANDRE")
    case = store.confirm("TL-ANDRE", "meets", "Alex Okonkwo")
    assert case.confirmed_rating == "meets"
    assert case.confirmed_by == "Alex Okonkwo"
    assert case.transmissions == []


def test_post_after_confirm_uses_the_manager_rating(store):
    run_case(store, "TL-MAYA")
    store.confirm("TL-MAYA", "meets", "Alex Okonkwo")
    case = store.post("TL-MAYA", "hr")
    assert case.performance.rating == "exceeds"
    assert case.transmissions[0].rating == "meets"
    assert "Confirmed rating: meets." in case.transmissions[0].body
    assert "system rating of exceeds" in case.transmissions[0].body
    case = store.post("TL-MAYA", "employee")
    assert [item.destination for item in case.transmissions] == ["hr", "employee"]


def test_stay_conversation_is_not_in_the_packet(store):
    run_case(store, "TL-ANDRE")
    draft = store.get_case("TL-ANDRE")
    assert draft.attrition.stay_conversation is not None
    assert STAY_MARKER in draft.attrition.stay_conversation
    store.confirm("TL-ANDRE", "meets", "Alex Okonkwo")
    case = store.post("TL-ANDRE", "employee")
    assert STAY_MARKER not in case.transmissions[0].body
    case = store.post("TL-ANDRE", "hr")
    assert STAY_MARKER not in case.transmissions[1].body


def test_rerun_clears_confirmation(store):
    run_case(store, "TL-JORDAN")
    store.confirm("TL-JORDAN", "below", "Alex Okonkwo")
    case = run_case(store, "TL-JORDAN")
    assert case.confirmed_rating is None
    assert case.transmissions == []
    with pytest.raises(RatingNotConfirmed):
        store.post("TL-JORDAN", "hr")


def test_api_refuses_to_post_before_confirm(store):
    run_case(store, "TL-MAYA")
    with TestClient(create_app(store)) as client:
        denied = client.post("/api/team/TL-MAYA/post", json={"destination": "hr"})
        assert denied.status_code == 409
        assert "confirm" in denied.json()["detail"].lower()
        assert client.get("/api/team/TL-MAYA").json()["transmissions"] == []

        confirmed = client.post(
            "/api/team/TL-MAYA/confirm",
            json={"rating": "exceeds", "manager": "Alex Okonkwo"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed_rating"] == "exceeds"
        assert confirmed.json()["transmissions"] == []

        posted = client.post("/api/team/TL-MAYA/post", json={"destination": "employee"})
        assert posted.status_code == 200
        body = posted.json()["transmissions"][0]
        assert body["destination"] == "employee"
        assert body["rating"] == "exceeds"
        assert STAY_MARKER not in body["body"]
