"""Command line surface: seed data, profile an account, print the sequence, run the eval."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import get_settings
from .data.repository import AccountRepository
from .data.seed import write_dataset
from .domain import RunResult
from .evaluation.harness import run_eval
from .graph import GraphDependencies, run_account
from .providers.tracing import read_trace

app = typer.Typer(add_completion=False, help="Multi-agent predictive dunning and collections assistant.")
# Piped output (CI, `| less`) otherwise wraps at 80 columns and mangles the tables.
console = Console(width=None if sys.stdout.isatty() else 130)

TONE_COLOUR = {"warm": "green", "neutral_professional": "cyan", "firm": "yellow", "formal_strict": "red"}
BAND_COLOUR = {"low": "green", "moderate": "cyan", "elevated": "yellow", "severe": "red"}


def _deps(sentiment_backend: Optional[str] = None, llm_provider: Optional[str] = None) -> GraphDependencies:
    settings = get_settings(sentiment_backend=sentiment_backend, llm_provider=llm_provider)
    return GraphDependencies.build(settings)


@app.command()
def seed(
    force: bool = typer.Option(False, "--force", help="Rewrite the tables even if they already exist."),
) -> None:
    """Generate the synthetic AR dataset (CSV + SQLite)."""
    settings = get_settings()
    if settings.sqlite_path.exists() and not force:
        console.print(f"[yellow]Dataset already present at {settings.data_dir} (use --force to regenerate).[/]")
        raise typer.Exit(0)
    path = write_dataset(settings)
    meta = json.loads((path / "meta.json").read_text())
    console.print(f"[green]Seeded[/] {meta['accounts']} accounts into {path} (as of {meta['as_of']})")
    console.print(f"  rows: {meta['row_counts']}")


@app.command()
def accounts(
    sentiment_backend: Optional[str] = typer.Option(None, "--sentiment-backend"),
) -> None:
    """List the seeded portfolio with aging and the ground-truth archetype."""
    repo = AccountRepository(get_settings(sentiment_backend=sentiment_backend))
    summary = repo.portfolio_summary()
    table = Table(title=f"Past-due portfolio as of {repo.as_of}", header_style="bold")
    for column in ("Account", "Customer", "Segment", "Open inv.", "Past due", "Oldest dpd", "Archetype (truth)"):
        table.add_column(column)
    for _, row in summary.iterrows():
        table.add_row(
            row["account_id"],
            row["name"],
            row["segment"],
            str(int(row["open_invoices"])),
            f"${row['past_due_balance']:,.0f}",
            str(int(row["oldest_days_past_due"])),
            row["archetype"],
        )
    console.print(table)


def _render(result: RunResult, show_bodies: bool) -> None:
    profile, sentiment, strategy = result.profile, result.sentiment, result.strategy
    metrics = profile.metrics
    band = BAND_COLOUR.get(profile.risk_band, "white")

    console.print(
        Panel(
            f"[bold]{result.customer.name}[/] ({result.account_id}) - {result.customer.segment}, "
            f"{result.customer.industry}\nAR owner {result.customer.ar_owner} | contact {result.customer.contact_name} "
            f"<{result.customer.contact_email}> | terms net {result.customer.payment_terms_days}",
            title=f"Account as of {result.as_of}",
            border_style="blue",
        )
    )

    profile_table = Table(show_header=False, box=None)
    profile_table.add_row("Archetype", f"[bold]{profile.archetype}[/]")
    profile_table.add_row("Risk", f"[{band}]{profile.risk_score:.0f}/100 ({profile.risk_band})[/]")
    profile_table.add_row("Payment rhythm", f"{profile.predictability}, expects ~{profile.expected_days_late:.0f} days late")
    profile_table.add_row("Outlook", profile.recovery_outlook.replace("_", " "))
    profile_table.add_row(
        "Aging",
        f"0-30 ${metrics.aging_0_30:,.0f} | 31-60 ${metrics.aging_31_60:,.0f} | "
        f"61-90 ${metrics.aging_61_90:,.0f} | 90+ ${metrics.aging_90_plus:,.0f}",
    )
    profile_table.add_row("Past due", f"${metrics.past_due_balance:,.0f} over {metrics.open_invoices} invoice(s), oldest {metrics.oldest_days_past_due} dpd")
    profile_table.add_row("History", f"{metrics.invoices_paid} paid, avg {metrics.avg_days_late:.0f} days late (stddev {metrics.days_late_stddev:.1f}), trend {metrics.lateness_trend:+.2f}/invoice")
    profile_table.add_row("Promises", f"{metrics.promises_broken} broken of {metrics.promises_made}")
    for signal in profile.signals:
        profile_table.add_row("", Text(f"- {signal}", style="dim"))
    console.print(Panel(profile_table, title="1. Behavioral profiler", border_style=band))

    sentiment_table = Table(show_header=False, box=None)
    sentiment_table.add_row("Relationship", f"[bold]{sentiment.relationship_label}[/] (health {sentiment.relationship_health:.0f}/100)")
    sentiment_table.add_row("Engagement", f"{sentiment.engagement_trend}, responsiveness {sentiment.responsiveness_score:.0f}/100")
    sentiment_table.add_row("Polarity", f"{sentiment.polarity_score:+.2f} from {len(sentiment.per_message)} client replies [dim](backend: {sentiment.backend})[/]")
    for quote in sentiment.evidence:
        sentiment_table.add_row("", Text(f"- {quote}", style="dim"))
    console.print(Panel(sentiment_table, title="2. Sentiment", border_style="magenta"))

    flags = [name for name in (
        "offer_payment_plan", "cite_contract_terms", "late_fee_warning", "service_hold_warning",
        "legal_referral", "human_approval_required", "escalate_to_owner",
    ) if getattr(strategy, name)]
    strategy_table = Table(show_header=False, box=None)
    strategy_table.add_row("Stage", f"[bold]{strategy.stage}[/]  (urgency {strategy.urgency})")
    strategy_table.add_row("Tone", f"[{TONE_COLOUR.get(strategy.tone, 'white')}]{strategy.tone}[/]")
    strategy_table.add_row("Channels", ", ".join(strategy.channels))
    strategy_table.add_row("Policies", ", ".join(flags) if flags else "none")
    for rule in strategy.policy_trace:
        strategy_table.add_row("", Text(f"- {rule}", style="dim"))
    console.print(Panel(strategy_table, title="3. Strategy decision", border_style=TONE_COLOUR.get(strategy.tone, "white")))

    steps = Table(header_style="bold")
    for column in ("#", "Day", "Send on", "Channel", "Tone", "Intent"):
        steps.add_column(column)
    for step in result.sequence.steps:
        steps.add_row(
            str(step.step_number), f"+{step.day_offset}", step.send_on.isoformat(),
            step.channel, step.tone, step.intent,
        )
    console.print(Panel(steps, title="4. Communication sequence", border_style="green"))

    if show_bodies:
        for step in result.sequence.steps:
            header = f"Step {step.step_number} - {step.channel} on {step.send_on} ({step.tone})"
            body = (f"Subject: {step.subject}\n\n" if step.subject else "") + step.body
            console.print(Panel(body, title=header, border_style="white"))

    review_style = "green" if result.review.passed else "red"
    review_text = (
        f"{result.review.checks_run} compliance checks passed"
        if result.review.passed
        else f"open issues: {'; '.join(result.review.issues)}"
    )
    console.print(Panel(
        f"[{review_style}]{review_text}[/]  (revisions: {result.revisions})\n"
        + ("[yellow]Sequence is held for human approval before sending.[/]\n" if strategy.human_approval_required else "")
        + f"[dim]trace: {result.trace_reference or 'disabled'}[/]",
        title="5. Compliance review",
        border_style=review_style,
    ))


@app.command()
def run(
    account_id: str = typer.Argument(..., help="Account to profile, e.g. ACC-2001"),
    full: bool = typer.Option(False, "--full", help="Print the full drafted message bodies."),
    as_json: bool = typer.Option(False, "--json", help="Emit the whole run as JSON."),
    sentiment_backend: Optional[str] = typer.Option(None, "--sentiment-backend", help="auto | huggingface | rules"),
    llm_provider: Optional[str] = typer.Option(None, "--llm", help="mock | openai"),
) -> None:
    """Profile one past-due account and draft its collection sequence."""
    deps = _deps(sentiment_backend, llm_provider)
    try:
        result = run_account(account_id.upper(), deps)
    except KeyError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    if as_json:
        console.print_json(json.dumps(result.to_public_dict(), default=str))
        return
    _render(result, show_bodies=full)
    console.print("\n".join(f"[dim]{line}[/]" for line in result.agent_log))


@app.command(name="eval")
def eval_command(
    out: Optional[Path] = typer.Option(None, "--out", help="Write the full report as JSON."),
    sentiment_backend: Optional[str] = typer.Option(None, "--sentiment-backend"),
    llm_provider: Optional[str] = typer.Option(None, "--llm"),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero if any case fails."),
) -> None:
    """Score generated sequences against the expected strategy for each archetype."""
    report = run_eval(_deps(sentiment_backend, llm_provider))
    table = Table(title=f"Sequence eval - {report.llm_provider} / sentiment {report.sentiment_backend}", header_style="bold")
    for column in ("Account", "Expected", "Predicted", "Sentiment", "Stage", "Tone", "Steps", "Score", "Failures"):
        table.add_column(column)
    for case in report.cases:
        table.add_row(
            case.account_id, case.expected_archetype, case.predicted_archetype, case.relationship_label,
            case.stage, case.tone, str(case.steps),
            f"[{'green' if case.passed else 'red'}]{case.score / case.max_score:.0%}[/]",
            "; ".join(case.failures) or "-",
        )
    console.print(table)

    dims = Table(title="Per-dimension pass rate", header_style="bold")
    dims.add_column("Dimension")
    dims.add_column("Pass rate")
    for name, value in report.by_dimension.items():
        dims.add_row(name, f"[{'green' if value == 1 else 'yellow' if value >= 0.8 else 'red'}]{value:.0%}[/]")
    console.print(dims)
    console.print(f"Overall weighted score [bold]{report.overall_score:.1%}[/], cases fully passing [bold]{report.pass_rate:.0%}[/]")

    if out:
        console.print(f"[green]Wrote[/] {report.write_json(out)}")
    if strict and report.pass_rate < 1:
        raise typer.Exit(1)


@app.command()
def trace(run_id: str = typer.Argument(..., help="Run id or path to a .jsonl trace file")) -> None:
    """Replay a locally recorded trace, span by span."""
    settings = get_settings()
    path = Path(run_id)
    if not path.exists():
        path = settings.trace_dir / f"{run_id}.jsonl"
    if not path.exists():
        console.print(f"[red]No local trace at {path}.[/] Available: {[p.stem for p in sorted(settings.trace_dir.glob('*.jsonl'))][-5:]}")
        raise typer.Exit(1)

    records = read_trace(path)
    header, spans = records[0], records[1:]
    console.print(Panel(json.dumps(header, indent=2), title=f"trace {path.name}", border_style="blue"))
    table = Table(header_style="bold")
    for column in ("Span", "ms", "Output"):
        table.add_column(column, overflow="fold")
    for span in spans:
        table.add_row(span["span"], f"{span['duration_ms']:.1f}", json.dumps(span["output"])[:220])
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8642, "--port", help="Uncommon default port to avoid collisions."),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the FastAPI service."""
    import uvicorn

    uvicorn.run("dunning.api:api", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
