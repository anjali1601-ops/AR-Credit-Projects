"""Command line for the same drafts the cockpit shows."""

from __future__ import annotations

from typing import Optional

import typer

from .case import run_all, run_case
from .config import MANAGER_NAME, PORT, db_path
from .errors import AlreadyPosted, RatingNotConfirmed
from .models import PeopleCase
from .store import Store

app = typer.Typer(add_completion=False, help="AR team-lead cockpit")


def _store() -> Store:
    store = Store(db_path())
    store.ensure_seeded()
    return store


def _resolve(store: Store, teammate: str) -> str:
    wanted = teammate.strip()
    ids = store.teammate_ids()
    if wanted in ids:
        return wanted
    upper = wanted.upper()
    if upper in ids:
        return upper
    known = ", ".join(ids)
    raise typer.BadParameter(f"Unknown teammate {teammate}. Known ids: {known}")


def _line(case: PeopleCase) -> str:
    flag = "flight risk" if case.attrition.flight_risk else "no flight risk"
    confirmed = case.confirmed_rating or "not confirmed"
    return (
        f"{case.name} ({case.teammate_id}): "
        f"rating={case.performance.rating} sentiment={case.sentiment.label} "
        f"{flag} confirmed={confirmed}"
    )


@app.command()
def seed() -> None:
    """Load the four seeded teammates, replacing the local case file."""
    store = Store(db_path())
    store.reset()
    typer.echo(f"Seeded {len(store.teammate_ids())} teammates into {store.path}")


@app.command()
def run(
    teammate: Optional[str] = typer.Argument(None, help="Teammate id, for example TL-MAYA."),
    all_: bool = typer.Option(False, "--all", help="Run every seeded teammate."),
) -> None:
    """Draft sentiment, a review, coaching, and a stay conversation when the rules say so."""
    store = _store()
    if all_:
        cases = run_all(store)
    elif teammate:
        cases = [run_case(store, _resolve(store, teammate))]
    else:
        typer.echo("Pass a teammate id or --all.")
        raise typer.Exit(code=1)
    for case in cases:
        typer.echo(_line(case))


@app.command()
def outcomes() -> None:
    """Print rating, sentiment, and flight risk for the four seeded teammates."""
    store = _store()
    for teammate_id in store.teammate_ids():
        case = store.get_case(teammate_id) or run_case(store, teammate_id)
        typer.echo(_line(case))


@app.command()
def show(teammate: str = typer.Argument(..., help="Teammate id, for example TL-ANDRE.")) -> None:
    """Print the drafted review, coaching, and stay conversation."""
    store = _store()
    teammate_id = _resolve(store, teammate)
    case = store.get_case(teammate_id) or run_case(store, teammate_id)
    typer.echo(_line(case))
    typer.echo("")
    typer.echo(case.sentiment.summary)
    typer.echo("")
    typer.echo(case.performance.narrative)
    typer.echo("")
    typer.echo(case.coaching)
    if case.attrition.stay_conversation:
        typer.echo("")
        typer.echo(case.attrition.stay_conversation)
    else:
        typer.echo("")
        typer.echo("No stay conversation.")
    if case.transmissions:
        typer.echo("")
        typer.echo("Sent: " + ", ".join(item.destination for item in case.transmissions))
    else:
        typer.echo("")
        typer.echo("Nothing sent to HR or the employee.")


@app.command()
def confirm(
    teammate: str = typer.Argument(...),
    rating: str = typer.Option(..., "--rating", help="exceeds, meets, or below"),
    manager: str = typer.Option(MANAGER_NAME, "--manager"),
) -> None:
    """Confirm the rating. This does not send anything."""
    store = _store()
    teammate_id = _resolve(store, teammate)
    if store.get_case(teammate_id) is None:
        run_case(store, teammate_id)
    try:
        case = store.confirm(teammate_id, rating, manager)  # type: ignore[arg-type]
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Confirmed {case.confirmed_rating} for {case.name} by {case.confirmed_by}. Nothing sent."
    )


@app.command()
def post(
    teammate: str = typer.Argument(...),
    to: str = typer.Option(..., "--to", help="hr or employee"),
) -> None:
    """Send the confirmed rating and review. Refuses when the rating is unconfirmed."""
    store = _store()
    teammate_id = _resolve(store, teammate)
    try:
        case = store.post(teammate_id, to)  # type: ignore[arg-type]
    except (RatingNotConfirmed, AlreadyPosted, KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"Sent {case.confirmed_rating} for {case.name} to {to}.")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(PORT, "--port"),
) -> None:
    """Serve the cockpit. Default port is 47261."""
    import uvicorn

    typer.echo(f"AR team-lead cockpit at http://127.0.0.1:{port}")
    uvicorn.run("team_lead.api:app", host=host, port=port, factory=False)


if __name__ == "__main__":
    app()
