"""Command line entry points: ``python -m dra <command>``."""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from dra.db.models import CaseDraft, DisputeCase, InboxMessage
from dra.db.seed import seed_all
from dra.db.session import session_scope
from dra.settings import get_settings
from dra.workflow import DisputeOrchestrator


def _cmd_seed(args: argparse.Namespace) -> int:
    info = seed_all(reset=not args.keep, with_inbox=not args.no_inbox)
    print(json.dumps(info, indent=2))
    return 0


def _cmd_poll(args: argparse.Namespace) -> int:
    results = DisputeOrchestrator().poll_inbox(limit=args.limit)
    print(json.dumps(results, indent=2, default=str))
    return 0


def _cmd_cases(args: argparse.Namespace) -> int:
    with session_scope() as session:
        cases = session.scalars(select(DisputeCase).order_by(DisputeCase.id)).all()
        for case in cases:
            amount = f"{case.disputed_amount:,.2f}" if case.disputed_amount else "-"
            print(
                f"{case.case_number}  {case.state:<18} {str(case.decision or '-'):<16} "
                f"{case.invoice_number or '-':<15} {amount:>12}  {case.reason_code}"
            )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    with session_scope() as session:
        case = session.scalar(
            select(DisputeCase).where(DisputeCase.case_number == args.case_number)
        )
        if case is None:
            print(f"no case {args.case_number}", file=sys.stderr)
            return 1
        print(f"{case.case_number}  [{case.state}]  decision={case.decision}")
        print(f"invoice={case.invoice_number}  amount={case.disputed_amount}")
        print(f"rationale: {case.decision_rationale}\n")
        print("timeline")
        for event in case.events:
            print(
                f"  {event.seq}. {event.from_state} -> {event.to_state} "
                f"({event.from_agent} -> {event.to_agent}): {event.summary}"
            )
        drafts = session.scalars(
            select(CaseDraft).where(CaseDraft.case_id == case.id)
        ).all()
        for draft in drafts:
            print(f"\n--- {draft.kind} [{draft.status}] to {draft.recipient}")
            print(f"Subject: {draft.subject}\n")
            print(draft.body)
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args, approve=True)


def _cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args, approve=False)


def _decide(args: argparse.Namespace, approve: bool) -> int:
    with session_scope() as session:
        case = session.scalar(
            select(DisputeCase).where(DisputeCase.case_number == args.case_number)
        )
        if case is None:
            print(f"no case {args.case_number}", file=sys.stderr)
            return 1
        case_id = case.id
    orchestrator = DisputeOrchestrator()
    action = orchestrator.approve if approve else orchestrator.reject
    print(json.dumps(action(case_id=case_id, actor=args.actor, note=args.note), indent=2))
    return 0


def _cmd_inbox(args: argparse.Namespace) -> int:
    with session_scope() as session:
        for message in session.scalars(
            select(InboxMessage).order_by(InboxMessage.received_at)
        ).all():
            print(
                f"{message.message_id:<34} {message.status:<10} {message.sender:<38} "
                f"{message.subject}"
            )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "dra.api.main:app",
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dra", description="Dispute resolution and negotiation agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="create the schema, fixtures and contract index")
    seed.add_argument("--keep", action="store_true", help="do not drop existing tables")
    seed.add_argument("--no-inbox", action="store_true", help="skip the sample emails")
    seed.set_defaults(func=_cmd_seed)

    poll = sub.add_parser("poll", help="process every unread email end to end")
    poll.add_argument("--limit", type=int, default=25)
    poll.set_defaults(func=_cmd_poll)

    cases = sub.add_parser("cases", help="list dispute cases")
    cases.set_defaults(func=_cmd_cases)

    show = sub.add_parser("show", help="show one case with its timeline and drafts")
    show.add_argument("case_number")
    show.set_defaults(func=_cmd_show)

    inbox = sub.add_parser("inbox", help="list the simulated inbox")
    inbox.set_defaults(func=_cmd_inbox)

    approve = sub.add_parser("approve", help="supervisor approval")
    approve.add_argument("case_number")
    approve.add_argument("--actor", default="cli-supervisor")
    approve.add_argument("--note", default="")
    approve.set_defaults(func=_cmd_approve)

    reject = sub.add_parser("reject", help="supervisor rejection")
    reject.add_argument("case_number")
    reject.add_argument("--actor", default="cli-supervisor")
    reject.add_argument("--note", default="")
    reject.set_defaults(func=_cmd_reject)

    serve = sub.add_parser("serve", help="run the FastAPI service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
