"""Command line for the surveillance desk."""

import argparse
import sqlite3
import sys
from decimal import Decimal, InvalidOperation

from credit_surveillance.db import connect, get_review, resolve_db_path
from credit_surveillance.errors import SurveillanceError
from credit_surveillance.seed import seed_database
from credit_surveillance.service import approve_decision, post_decision, request_limit, run_surveillance

DEMO_OUTCOMES = {
    "NW-1044": "affirm",
    "HB-2201": "reduce",
    "VP-3310": "conditions",
    "RL-4408": "suspend",
}


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        db_path, cleaned = _extract_db(raw)
    except ValueError as exc:
        print(exc)
        return 2
    parser = _parser()
    args = parser.parse_args(cleaned)
    args.db = db_path
    try:
        return args.func(args)
    except SurveillanceError as exc:
        print(exc)
        return 1


def _extract_db(argv: list[str]) -> tuple[str | None, list[str]]:
    """Accept ``--db`` before or after the subcommand."""
    db_path = None
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--db":
            if index + 1 >= len(argv):
                raise ValueError("credit-surveillance: --db requires a path")
            db_path = argv[index + 1]
            index += 2
            continue
        if token.startswith("--db="):
            db_path = token.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(token)
        index += 1
    return db_path, cleaned


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="credit-surveillance",
        description="Watch an existing B2B credit portfolio and draft periodic reviews.",
    )
    parser.add_argument("--db", help="SQLite path. Defaults to credit_surveillance/data/portfolio.db")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Load the four demonstration accounts")
    seed.set_defaults(func=_cmd_seed)

    review = sub.add_parser("review", help="Draft periodic-review memos")
    review.add_argument("--account", help="Limit the run to one account id")
    review.set_defaults(func=_cmd_review)

    show = sub.add_parser("show", help="Print one memo")
    show.add_argument("review_id")
    show.set_defaults(func=_cmd_show)

    post = sub.add_parser("post", help="Post a recommendation that is inside analyst authority")
    post.add_argument("review_id")
    post.add_argument("--actor", required=True)
    post.add_argument("--role", default="analyst")
    post.set_defaults(func=_cmd_post)

    approve = sub.add_parser("approve", help="Approve a gated recommendation as a named credit manager")
    approve.add_argument("review_id")
    approve.add_argument("--approver", required=True)
    approve.add_argument("--role", default="credit_manager")
    approve.set_defaults(func=_cmd_approve)

    request = sub.add_parser("request-limit", help="Ask surveillance to consider a higher limit")
    request.add_argument("account_id")
    request.add_argument("amount")
    request.set_defaults(func=_cmd_request)

    serve = sub.add_parser("serve", help="Run the HTTP desk")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=47231)
    serve.set_defaults(func=_cmd_serve)

    demo = sub.add_parser("demo", help="Reseed and draft the four outcomes")
    demo.set_defaults(func=_cmd_demo)
    return parser


def _cmd_seed(args) -> int:
    with _session(args) as conn:
        ids = seed_database(conn)
    print(f"Seeded {len(ids)} accounts: {', '.join(ids)}")
    return 0


def _cmd_review(args) -> int:
    with _session(args) as conn:
        reviews = run_surveillance(conn, account_id=args.account)
    for review in reviews:
        _print_review(review)
    return 0


def _cmd_show(args) -> int:
    with _session(args) as conn:
        review = get_review(conn, args.review_id)
    if review is None:
        print(f"No review {args.review_id}.")
        return 1
    _print_review(review, narrative=True)
    return 0


def _cmd_post(args) -> int:
    with _session(args) as conn:
        review = post_decision(
            conn,
            args.review_id,
            actor_name=args.actor,
            actor_role=args.role,
        )
    print(f"Posted {review.id} for {review.account_id} by {review.posted_by}.")
    return 0


def _cmd_approve(args) -> int:
    with _session(args) as conn:
        review = approve_decision(
            conn,
            args.review_id,
            approver_name=args.approver,
            approver_role=args.role,
        )
    print(
        f"Approved {review.id} for {review.account_id} "
        f"by {review.approver_name} ({review.approver_role})."
    )
    return 0


def _cmd_request(args) -> int:
    try:
        amount = Decimal(args.amount)
    except InvalidOperation:
        print("Amount must be a decimal string, for example 120000.00.")
        return 1
    with _session(args) as conn:
        account = request_limit(conn, args.account_id, amount)
    print(
        f"{account.id} requested limit {account.requested_limit:.2f}. "
        "Run review to draft the memo."
    )
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    from credit_surveillance.api import create_app

    uvicorn.run(create_app(resolve_db_path(args.db)), host=args.host, port=args.port)
    return 0


def _cmd_demo(args) -> int:
    with _session(args) as conn:
        seed_database(conn)
        reviews = run_surveillance(conn)
    mismatch = False
    for review in reviews:
        expected = DEMO_OUTCOMES.get(review.account_id)
        _print_review(review, narrative=True)
        if review.action != expected:
            print(f"Expected {review.account_id} to be {expected}.")
            mismatch = True
    if mismatch:
        return 1
    print("Demo drafted affirm, reduce, conditions, and suspend.")
    return 0


def _print_review(review, narrative: bool = False) -> None:
    gate = "pending approval" if review.authority.requires_approver else "within authority"
    print(
        f"{review.account_id}  {review.account_name}  {review.action.upper()}  "
        f"{review.current_limit:.2f} -> {review.proposed_limit:.2f}  "
        f"{gate}  {review.status}  {review.id}"
    )
    if narrative:
        print(review.narrative)
        print()


class _session:
    def __init__(self, args) -> None:
        self.args = args
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect(resolve_db_path(self.args.db))
        return self.conn

    def __exit__(self, exc_type, exc, _tb):
        if self.conn is not None:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()
        return False
