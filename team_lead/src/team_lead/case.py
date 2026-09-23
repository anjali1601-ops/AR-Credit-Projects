"""Run the three agents over one teammate and keep a single case."""

from __future__ import annotations

from .agents.attrition import assess_attrition
from .agents.performance import draft_performance, money, rate_pct
from .agents.sentiment import assess_sentiment
from .config import PERIOD
from .llm import LLMProvider, LLMRequest, ensure_contains, get_llm
from .metrics import build_snapshot
from .models import PeopleCase, SourceNote, TeamCard
from .store import Store


def _coaching_kind(case_rating: str, ramp: bool, flight_risk: bool) -> str:
    if flight_risk:
        return "load"
    if case_rating == "exceeds":
        return "stretch"
    if case_rating == "below":
        return "fair"
    if ramp:
        return "ramp"
    return "hold"


def run_case(store: Store, teammate_id: str, llm: LLMProvider | None = None) -> PeopleCase:
    """Rebuild the draft. A new run clears any earlier confirmation."""
    writer = llm or get_llm()
    teammate = store.get_teammate(teammate_id)
    notes = [
        SourceNote(source="one_on_one", observed_on=item.met_on, text=item.note)
        for item in store.one_on_ones(teammate_id)
    ]
    reviews = store.qa_reviews(teammate_id)
    notes.extend(
        SourceNote(source="qa", observed_on=item.reviewed_on, text=item.comment) for item in reviews
    )
    snapshot = build_snapshot(
        tenure_months=teammate.tenure_months,
        cash=store.cash_applications(teammate_id),
        promises=store.promises(teammate_id),
        disputes=store.disputes(teammate_id),
        reviews=reviews,
        overtime_hours=store.overtime_hours(teammate_id),
        cases_closed=store.cases_closed(teammate_id),
    )
    sentiment = assess_sentiment(teammate.name, notes)
    performance = draft_performance(teammate, snapshot, PERIOD, writer)
    attrition = assess_attrition(teammate, snapshot, sentiment, performance.rating, writer)
    kind = _coaching_kind(performance.rating, snapshot.ramp, attrition.flight_risk)
    facts = {
        "kind": kind,
        "name": teammate.name,
        "cases": snapshot.cases_closed,
        "expectation": snapshot.workload_expectation,
        "ot_avg": f"{snapshot.average_weekly_overtime:.1f}",
        "ot_weeks": snapshot.overtime_weeks_high,
        "quality": f"{snapshot.quality_score:.1f}",
        "cash": money(snapshot.cash_applied),
        "cash_target": money(snapshot.cash_target),
        "promises": f"{snapshot.promises_kept} of {snapshot.promises_made}",
        "promise_pct": rate_pct(snapshot.promise_kept_rate),
        "cycle": f"{snapshot.dispute_cycle_days:.1f} days",
        "tenure_months": teammate.tenure_months,
    }
    coaching = writer.complete(
        LLMRequest(
            task="coaching",
            prompt=(
                f"Write coaching for {teammate.name}. Kind is {kind}. "
                f"Use only these figures: {facts}"
            ),
            facts=facts,
        )
    )
    coaching = ensure_contains(coaching.strip(), [f"{snapshot.cases_closed} cases"])
    case = PeopleCase(
        teammate_id=teammate.id,
        name=teammate.name,
        role=teammate.role,
        tenure_months=teammate.tenure_months,
        scenario=teammate.scenario,
        scenario_label=teammate.scenario_label,
        period=PERIOD,
        metrics=snapshot,
        sentiment=sentiment,
        performance=performance,
        attrition=attrition,
        coaching=coaching.strip(),
        coaching_provider=writer.name,
    )
    return store.save_case(case)


def run_all(store: Store, llm: LLMProvider | None = None) -> list[PeopleCase]:
    return [run_case(store, teammate_id, llm) for teammate_id in store.teammate_ids()]


def prepare_demo(store: Store) -> None:
    store.ensure_seeded()
    if not store.any_cases():
        run_all(store)


def team_cards(store: Store) -> list[TeamCard]:
    cards: list[TeamCard] = []
    for teammate in store.list_teammates():
        case = store.get_case(teammate.id)
        cards.append(
            TeamCard(
                teammate_id=teammate.id,
                name=teammate.name,
                role=teammate.role,
                tenure_months=teammate.tenure_months,
                scenario=teammate.scenario,
                scenario_label=teammate.scenario_label,
                sentiment=case.sentiment.label if case else None,
                rating=case.performance.rating if case else None,
                flight_risk=case.attrition.flight_risk if case else None,
                confirmed_rating=case.confirmed_rating if case else None,
                sent=[item.destination for item in case.transmissions] if case else [],
            )
        )
    return cards
