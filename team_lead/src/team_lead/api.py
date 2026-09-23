"""HTTP cockpit on the same case runner the CLI uses."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .case import prepare_demo, run_all, run_case, team_cards
from .config import PERIOD, PORT, db_path, llm_provider_name
from .errors import AlreadyPosted, RatingNotConfirmed
from .models import Destination, PeopleCase, Rating, TeamCard
from .store import Store

INDEX = Path(__file__).resolve().parent / "static" / "index.html"


class ConfirmIn(BaseModel):
    rating: Rating
    manager: str = Field(min_length=1)


class PostIn(BaseModel):
    destination: Destination


def create_app(store: Store | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            live = Store(db_path())
            prepare_demo(live)
            app.state.store = live
        yield

    app = FastAPI(
        title="AR Team-Lead Cockpit",
        version="0.1.0",
        description=(
            "Coaching, performance reviews, and stay conversations for an "
            "accounts-receivable and credit team. Nothing is sent until the manager confirms the rating."
        ),
        lifespan=lifespan,
    )
    app.state.store = store

    def current() -> Store:
        return app.state.store

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return INDEX.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict:
        live = current()
        return {
            "status": "ok",
            "period": PERIOD,
            "port": PORT,
            "llm_provider": llm_provider_name(),
            "teammates": len(live.teammate_ids()),
        }

    @app.get("/api/team", response_model=list[TeamCard])
    def list_team() -> list[TeamCard]:
        return team_cards(current())

    @app.get("/api/team/{teammate_id}", response_model=PeopleCase)
    def get_member(teammate_id: str) -> PeopleCase:
        try:
            return current().require_case(teammate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No case for {teammate_id}.") from exc

    @app.post("/api/team/{teammate_id}/run", response_model=PeopleCase)
    def run_member(teammate_id: str) -> PeopleCase:
        try:
            return run_case(current(), teammate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown teammate {teammate_id}.") from exc

    @app.post("/api/team/{teammate_id}/confirm", response_model=PeopleCase)
    def confirm_member(teammate_id: str, body: ConfirmIn) -> PeopleCase:
        try:
            return current().confirm(teammate_id, body.rating, body.manager)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No case for {teammate_id}.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/team/{teammate_id}/post", response_model=PeopleCase)
    def post_member(teammate_id: str, body: PostIn) -> PeopleCase:
        try:
            return current().post(teammate_id, body.destination)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No case for {teammate_id}.") from exc
        except RatingNotConfirmed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AlreadyPosted as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/reset", response_model=list[TeamCard])
    def reset_demo() -> list[TeamCard]:
        live = current()
        live.reset()
        run_all(live)
        return team_cards(live)

    return app


app = create_app()
