"""Command line for seeding, reviewing, and confirming cash applications."""

from __future__ import annotations

import argparse
import sys

from cash_application.db import get_engine, init_db, session_scope
from cash_application.models import Base
from cash_application.seed import PRIMARY_REFS
from cash_application.service import (
    ConfirmError,
    confirm,
    customer_count,
    list_proposals,
    load_seed,
    process_pending,
    proposal_payload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cashapp",
        description="Match lockbox, EDI 820, and short-pay remittances to open invoices.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Load the Harborline AR ledger and remittances")
    seed.add_argument("--reset", action="store_true", help="Drop the SQLite file's tables first")
    seed.set_defaults(func=cmd_seed)

    process = sub.add_parser("process", help="Run ingestion, matching, and exception routing")
    process.set_defaults(func=cmd_process)

    queue = sub.add_parser("queue", help="Show ready items and exceptions")
    queue.set_defaults(func=cmd_queue)

    show = sub.add_parser("show", help="Show one remittance proposal")
    show.add_argument("ref")
    show.set_defaults(func=cmd_show)

    confirm_cmd = sub.add_parser("confirm", help="Post a proposal. Requires a clerk name.")
    confirm_cmd.add_argument("ref")
    confirm_cmd.add_argument("--action", required=True, choices=["apply", "split", "leave_unapplied"])
    confirm_cmd.add_argument("--clerk", required=True)
    confirm_cmd.add_argument(
        "--lines",
        help="Split allocations as INV-10550:5600.00,INV-10558:500.00",
    )
    confirm_cmd.set_defaults(func=cmd_confirm)

    outcomes = sub.add_parser("outcomes", help="Print the seeded proposals and check the five types")
    outcomes.set_defaults(func=cmd_outcomes)

    serve = sub.add_parser("serve", help="Clerk workbench on port 47221")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=47221)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


def _ensure(session, reset: bool = False) -> None:
    if reset:
        return
    if customer_count(session) == 0:
        load_seed(session)


def cmd_seed(args: argparse.Namespace) -> int:
    if args.reset:
        init_db()
        Base.metadata.drop_all(get_engine())
        Base.metadata.create_all(get_engine())
    with session_scope() as session:
        if args.reset or customer_count(session) == 0:
            load_seed(session)
            created = True
        else:
            created = False
        processed = process_pending(session)
    if created:
        print(f"Seeded Harborline AR and processed {processed} remittances.")
    else:
        print(f"Ledger already seeded. Processed {processed} pending remittances. Use --reset to rebuild.")
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    with session_scope() as session:
        _ensure(session)
        count = process_pending(session)
    print(f"Processed {count} remittances.")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    with session_scope() as session:
        _ensure(session)
        process_pending(session)
        payloads = [proposal_payload(proposal) for proposal in list_proposals(session)]
    for bucket, title in (("ready", "Ready to confirm"), ("exception", "Exceptions")):
        print(f"\n{title}")
        rows = [item for item in payloads if item["queue"] == bucket and item["status"] != "posted"]
        if not rows:
            print("  (none)")
            continue
        for item in rows:
            print(
                f"  {item['external_ref']:<24} {item['kind']:<12} "
                f"{item['amount']:>10}  conf {item['confidence']}  {item['payer_name']}"
            )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with session_scope() as session:
        _ensure(session)
        process_pending(session)
        payload = next(
            (proposal_payload(proposal) for proposal in list_proposals(session) if proposal.remittance.external_ref == args.ref),
            None,
        )
    if payload is None:
        print(f"No proposal for {args.ref}.", file=sys.stderr)
        return 1
    _print_detail(payload)
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    split_lines = _parse_lines(args.lines) if args.lines else None
    with session_scope() as session:
        _ensure(session)
        process_pending(session)
        try:
            receipt = confirm(session, args.ref, args.action, args.clerk, split_lines)
        except ConfirmError as exc:
            print(exc.detail, file=sys.stderr)
            return 1
        print(
            f"Posted {args.ref}: {receipt.status} "
            f"applied {receipt.applied_amount} unapplied {receipt.unapplied_amount} by {receipt.posted_by}"
        )
    return 0


def cmd_outcomes(args: argparse.Namespace) -> int:
    with session_scope() as session:
        _ensure(session)
        process_pending(session)
        payloads = [proposal_payload(proposal) for proposal in list_proposals(session)]
    print(
        f"{'ref':<24} {'channel':<10} {'kind':<12} {'lines':>5} "
        f"{'queue':<10} {'conf':>5} {'unapplied':>10} {'shortfall':>10} action"
    )
    by_ref = {item["external_ref"]: item for item in payloads}
    for item in payloads:
        print(
            f"{item['external_ref']:<24} {item['channel']:<10} {item['kind']:<12} {item['line_count']:>5} "
            f"{item['queue']:<10} {item['confidence']:>5} {item['unapplied_cash']:>10} "
            f"{item['short_fall']:>10} {item['recommended_action']}"
        )
    signatures = []
    for ref in PRIMARY_REFS:
        item = by_ref[ref]
        signatures.append(
            (
                item["kind"],
                item["line_count"],
                item["queue"],
                item["unapplied_cash"] != "0.00",
                item["short_fall"] != "0.00",
            )
        )
    if len(set(signatures)) != len(PRIMARY_REFS):
        print("The five remittance types collided.", file=sys.stderr)
        return 1
    print("Five remittance types are distinct.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("cash_application.api:app", host=args.host, port=args.port, reload=False)
    return 0


def _parse_lines(text: str) -> list[dict]:
    lines = []
    for part in text.split(","):
        number, amount = part.split(":", 1)
        lines.append({"invoice_number": number.strip(), "amount": amount.strip()})
    return lines


def _print_detail(payload: dict) -> None:
    print(f"{payload['external_ref']}  {payload['channel']}  {payload['kind']}  queue {payload['queue']}")
    print(f"Payer: {payload['payer_name']}  {payload['payer_account'] or 'no account'}  amount {payload['amount']}")
    print(f"Confidence {payload['confidence']}  recommended {payload['recommended_action']}")
    print(payload["route_reason"])
    print()
    print(payload["explanation"])
    print()
    if payload["lines"]:
        print("Proposed")
        for line in payload["lines"]:
            print(
                f"  {line['invoice_number']}  apply {line['proposed_amount']}  "
                f"open {line['open_amount']}  score {line['score']}"
            )
    print("Candidates")
    for candidate in payload["candidates"]:
        mark = "*" if candidate["selected"] else " "
        print(
            f" {mark} {candidate['invoice_number']}  score {candidate['score']:>3}  "
            f"{', '.join(candidate['reason_labels'])}"
        )
    if payload["receipt"]:
        receipt = payload["receipt"]
        print()
        print(f"Posted by {receipt['posted_by']} as {receipt['status']} via {receipt['action']}")


if __name__ == "__main__":
    raise SystemExit(main())
