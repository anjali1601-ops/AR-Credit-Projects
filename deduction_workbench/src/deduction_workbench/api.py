"""HTTP desk for the deduction workbench. Default port is 47241."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from deduction_workbench import __version__
from deduction_workbench.config import get_settings
from deduction_workbench.db import connect, get_view, list_views, sent_count
from deduction_workbench.llm.base import get_narrator
from deduction_workbench.pipeline import ConfirmError, confirm_case, prepare, process_case
from deduction_workbench.ui import render_case, render_not_found, render_queue


class ConfirmIn(BaseModel):
    clerk_id: str = Field(..., min_length=1, max_length=80)
    note: str = ""
    queue: str | None = None


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = get_settings()
    path = Path(db_path) if db_path is not None else settings.db_path
    narrator = get_narrator(settings)
    conn = prepare(path, narrator)
    conn.close()

    app = FastAPI(
        title="Deduction Workbench",
        version=__version__,
        description=(
            "Classify US trade deductions, check them against the contract or "
            "promo agreement, and hold the route for a clerk. Nothing is sent automatically."
        ),
    )
    app.state.db_path = str(path)
    app.state.llm_provider = narrator.name

    @app.get("/health")
    def health() -> dict:
        with _conn(app) as connection:
            cases = list_views(connection)
            return {
                "status": "ok",
                "llm_provider": app.state.llm_provider,
                "cases": len(cases),
                "awaiting_confirmation": sum(
                    1 for case in cases if case["status"] == "awaiting_confirmation"
                ),
                "confirmed": sum(1 for case in cases if case["status"] == "confirmed"),
                "sent": sent_count(connection),
            }

    @app.get("/api/cases")
    def api_cases() -> list[dict]:
        with _conn(app) as connection:
            return list_views(connection)

    @app.get("/api/cases/{case_id}")
    def api_case(case_id: str) -> dict:
        with _conn(app) as connection:
            view = get_view(connection, case_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"No deduction {case_id}.")
        return view

    @app.post("/api/cases/{case_id}/run")
    def api_run(case_id: str) -> dict:
        with _conn(app) as connection:
            try:
                return process_case(connection, case_id)
            except ConfirmError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/api/cases/{case_id}/confirm")
    def api_confirm(case_id: str, body: ConfirmIn) -> dict:
        with _conn(app) as connection:
            try:
                return confirm_case(
                    connection,
                    case_id,
                    clerk_id=body.clerk_id,
                    note=body.note,
                    queue=body.queue,
                )
            except ConfirmError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.get("/", response_class=HTMLResponse)
    def queue_page() -> HTMLResponse:
        with _conn(app) as connection:
            cases = list_views(connection)
            sent = sent_count(connection)
        return HTMLResponse(render_queue(cases, sent))

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(case_id: str, confirmed: int = 0) -> HTMLResponse:
        with _conn(app) as connection:
            view = get_view(connection, case_id)
        if view is None:
            return HTMLResponse(render_not_found(case_id), status_code=404)
        return HTMLResponse(render_case(view, just_confirmed=bool(confirmed)))

    @app.post("/cases/{case_id}/confirm", response_model=None)
    def confirm_form(
        case_id: str,
        clerk_id: str = Form(...),
        note: str = Form(""),
        queue: str = Form(""),
    ) -> Response:
        with _conn(app) as connection:
            try:
                confirm_case(connection, case_id, clerk_id=clerk_id, note=note, queue=queue or None)
            except ConfirmError as exc:
                view = get_view(connection, case_id)
                if view is None:
                    return HTMLResponse(render_not_found(case_id), status_code=exc.status_code)
                return HTMLResponse(
                    render_case(view, error=exc.detail),
                    status_code=exc.status_code,
                )
        return RedirectResponse(url=f"/cases/{case_id}?confirmed=1", status_code=303)

    return app


class _conn:
    """Short-lived connection so a confirm and a read do not share a stale handle."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.connection = None

    def __enter__(self):
        self.connection = connect(self.app.state.db_path)
        return self.connection

    def __exit__(self, *exc: object) -> None:
        if self.connection is not None:
            self.connection.close()


app = create_app()
