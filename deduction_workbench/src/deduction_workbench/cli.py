"""Command line for the same desk the API serves."""

from __future__ import annotations

import argparse
import sqlite3

from deduction_workbench.config import get_settings
from deduction_workbench.db import get_view, list_views, sent_count
from deduction_workbench.pipeline import ConfirmError, confirm_case, prepare, process_case, process_pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deduction-workbench",
        description="Classify trade deductions and hold the route for a clerk.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="Load the sample deductions and route the new ones")
    sub.add_parser("list", help="Show every case, its reason, and its route")
    sub.add_parser("routes", help="Show that each reason code takes a different desk")

    show = sub.add_parser("show", help="Print one case")
    show.add_argument("case_id")

    run = sub.add_parser("run", help="Classify and route a case, or every new case")
    run.add_argument("case_id", nargs="?")
    run.add_argument("--all", action="store_true")

    confirm = sub.add_parser("confirm", help="Record a clerk's confirmation. Does not send anything.")
    confirm.add_argument("case_id")
    confirm.add_argument("--clerk", required=True)
    confirm.add_argument("--note", default="")
    confirm.add_argument("--queue", default=None, help="Override the proposed queue")

    serve = sub.add_parser("serve", help="Serve the clerk desk (default port 47241)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.cmd == "serve":
        return _serve(args.host or settings.host, args.port or settings.port)

    conn = prepare(settings.db_path)
    try:
        if args.cmd == "seed":
            pending = process_pending(conn)
            print(f"Seeded {settings.db_path}. Newly routed: {len(pending)}. Nothing sent.")
            return 0
        if args.cmd == "list":
            return _list(conn)
        if args.cmd == "routes":
            return _routes(conn)
        if args.cmd == "show":
            return _show(conn, args.case_id)
        if args.cmd == "run":
            return _run(conn, args.case_id, args.all)
        if args.cmd == "confirm":
            return _confirm(conn, args.case_id, args.clerk, args.note, args.queue)
    finally:
        conn.close()
    return 2


def _list(conn: sqlite3.Connection) -> int:
    rows = list_views(conn)
    if not rows:
        print("No deductions. Run: python -m deduction_workbench seed")
        return 0
    print(f"{'Case':<12} {'Reason':<20} {'Outcome':<9} {'Queue':<12} {'Status':<24} Sent")
    for row in rows:
        sent = "yes" if row["sent"] else "no"
        print(
            f"{row['id']:<12} {(row['reason_label'] or '—'):<20} "
            f"{(row['outcome'] or '—'):<9} {(row['queue'] or '—'):<12} "
            f"{row['status']:<24} {sent}"
        )
    print(f"\n{len(rows)} cases. Sent: {sent_count(conn)}.")
    return 0


def _routes(conn: sqlite3.Connection) -> int:
    rows = list_views(conn)
    print(f"{'Case':<12} {'Reason':<20} {'Outcome':<9} {'Queue':<12} Citation")
    valid_queues: dict[str, str] = {}
    for row in rows:
        print(
            f"{row['id']:<12} {(row['reason_code'] or '—'):<20} "
            f"{(row['outcome'] or '—'):<9} {(row['queue'] or '—'):<12} "
            f"{row['citation_id'] or '—'}"
        )
        if row["outcome"] == "valid" and row["reason_code"]:
            valid_queues[row["reason_code"]] = row["queue"]
    queues = list(valid_queues.values())
    distinct = len(queues) == len(set(queues)) and len(queues) >= 5
    print()
    print("Valid routes: " + ", ".join(f"{reason} -> {queue}" for reason, queue in sorted(valid_queues.items())))
    print("Each reason code routes differently: " + ("yes" if distinct else "no"))
    print(f"Sent: {sent_count(conn)}.")
    return 0 if distinct else 1


def _show(conn: sqlite3.Connection, case_id: str) -> int:
    view = get_view(conn, case_id)
    if view is None:
        print(f"No deduction {case_id}.")
        return 1
    print(f"{view['id']}  {view['customer_name']}  {view['debit_memo']}")
    print(f"Amount: ${view['claimed_amount']:,.2f}  Invoice: {view['invoice_number']}")
    print(f"Reason: {view['reason_label']} ({view['reason_code']})  confidence {view['confidence']}")
    print(f"Outcome: {view['outcome']}")
    print(f"Route: {view['queue_label']} [{view['queue']}]  org {view['organization']} / {view['desk']}")
    print(f"Status: {view['status']}  sent: {'yes' if view['sent'] else 'no'}  auto-send: no")
    print(f"Citation: {view['citation_id']}")
    print(view["citation_text"])
    print()
    for check in view["checks"]:
        mark = "pass" if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['name']}: {check['detail']}")
    print()
    print(view["narrative"])
    if view["confirmed_by"]:
        print(f"\nConfirmed by {view['confirmed_by']}. Nothing was sent.")
    return 0


def _run(conn: sqlite3.Connection, case_id: str | None, run_all: bool) -> int:
    try:
        if run_all or not case_id:
            done = process_pending(conn)
            print(f"Routed {len(done)} new case(s). Nothing sent.")
            return 0
        view = process_case(conn, case_id)
    except ConfirmError as exc:
        print(exc.detail)
        return 1
    print(f"{view['id']} -> {view['queue']} ({view['outcome']}). Nothing sent.")
    return 0


def _confirm(conn: sqlite3.Connection, case_id: str, clerk: str, note: str, queue: str | None) -> int:
    try:
        view = confirm_case(conn, case_id, clerk_id=clerk, note=note, queue=queue)
    except ConfirmError as exc:
        print(exc.detail)
        return 1
    print(
        f"Confirmed {view['id']} to {view['queue']} by {view['confirmed_by']}. "
        "Nothing was sent."
    )
    return 0


def _serve(host: str, port: int) -> int:
    import uvicorn

    print(f"Deduction Workbench at http://{host}:{port}")
    print("Confirming a route does not send anything.")
    uvicorn.run("deduction_workbench.api:app", host=host, port=port, factory=False)
    return 0
