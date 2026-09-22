"""HTTP control tower. The book is the seed; every request recomputes it."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from o2c_control_tower.render import render_html
from o2c_control_tower.serialize import control_tower_payload
from o2c_control_tower.summary import build_control_tower

HOST = "0.0.0.0"
PORT = 47251


def create_app() -> FastAPI:
    app = FastAPI(
        title="O2C Control Tower",
        version="0.1.0",
        summary="Aging, DSO bridge, 14-day cash, collector workload, and the human queue.",
    )

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return render_html(build_control_tower())

    @app.get("/api/health")
    def health() -> dict:
        tower = build_control_tower()
        return {"status": "ok", "as_of": tower.as_of.isoformat()}

    @app.get("/api/summary")
    def summary() -> dict:
        return control_tower_payload(build_control_tower())

    @app.get("/api/aging")
    def aging() -> dict:
        return control_tower_payload(build_control_tower())["aging"]

    @app.get("/api/dso")
    def dso() -> dict:
        return control_tower_payload(build_control_tower())["dso_bridge"]

    @app.get("/api/cash-forecast")
    def cash_forecast() -> dict:
        return control_tower_payload(build_control_tower())["cash_forecast"]

    @app.get("/api/workload")
    def workload() -> dict:
        payload = control_tower_payload(build_control_tower())
        return {"collectors": payload["collectors"]}

    @app.get("/api/actions")
    def actions() -> dict:
        payload = control_tower_payload(build_control_tower())
        return {"actions": payload["actions"]}

    return app


app = create_app()
