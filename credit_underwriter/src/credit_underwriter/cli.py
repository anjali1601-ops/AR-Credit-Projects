"""Command line interface.

    credit-underwriter applicants                  list the seeded applicants
    credit-underwriter seed                        build the index and report on the corpus
    credit-underwriter underwrite <applicant_id>   underwrite one applicant, write the memo
    credit-underwriter underwrite-all              underwrite every seeded applicant
    credit-underwriter runs                        list persisted runs
    credit-underwriter show <run_id>               print a persisted memo
    credit-underwriter verify <run_id>             re-run and compare the state hash
    credit-underwriter serve                       start the API
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agents.context import GraphContext
from .applicants import ApplicantNotFound, get_application, list_applications
from .config import DEFAULT_API_PORT, Settings, ensure_dirs
from .models import format_currency
from .persistence import list_runs, load_run
from .retrieval import build_index, corpus_hash, load_corpus
from .service import replay, underwrite, verify

RULE = "─" * 78


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = _settings_from_args(args)
    ensure_dirs(settings)

    handlers = {
        "applicants": _cmd_applicants,
        "seed": _cmd_seed,
        "underwrite": _cmd_underwrite,
        "underwrite-all": _cmd_underwrite_all,
        "runs": _cmd_runs,
        "show": _cmd_show,
        "verify": _cmd_verify,
        "serve": _cmd_serve,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args, settings)
    except ApplicantNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="credit-underwriter",
        description="Supervisor-led multi-agent credit underwriting for B2B onboarding.",
    )
    parser.add_argument(
        "--provider",
        choices=["offline", "openai"],
        help="LLM provider (default offline, needs no API key)",
    )
    parser.add_argument("--model", help="model name for the chosen provider")
    parser.add_argument(
        "--retrieval-backend",
        choices=["chroma", "in_memory"],
        help="vector index backend (default chroma)",
    )
    parser.add_argument("--as-of", help="valuation date for document recency, YYYY-MM-DD")
    parser.add_argument("--data-dir", type=Path, help="override the seed data directory")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("applicants", help="list the seeded applicants")
    sub.add_parser("seed", help="build the retrieval index and report on the corpus")

    underwrite_cmd = sub.add_parser("underwrite", help="underwrite one applicant")
    underwrite_cmd.add_argument("applicant_id")
    underwrite_cmd.add_argument(
        "--out", type=Path, help="directory for the memo (default <project>/memos)"
    )
    underwrite_cmd.add_argument("--print-memo", action="store_true", help="print the memo")
    underwrite_cmd.add_argument("--json", action="store_true", help="print the decision as JSON")

    all_cmd = sub.add_parser("underwrite-all", help="underwrite every seeded applicant")
    all_cmd.add_argument("--out", type=Path, help="directory for the memos")

    sub.add_parser("runs", help="list persisted runs")

    show_cmd = sub.add_parser("show", help="print a persisted memo")
    show_cmd.add_argument("run_id")

    verify_cmd = sub.add_parser("verify", help="re-run a persisted underwriting and compare")
    verify_cmd.add_argument("run_id")

    serve_cmd = sub.add_parser("serve", help="start the FastAPI service")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=None)
    serve_cmd.add_argument("--reload", action="store_true")
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if getattr(args, "provider", None):
        overrides["llm_provider"] = args.provider
        if args.provider == "openai" and not getattr(args, "model", None):
            overrides["llm_model"] = "gpt-4o-mini"
    if getattr(args, "model", None):
        overrides["llm_model"] = args.model
    if getattr(args, "retrieval_backend", None):
        overrides["retrieval_backend"] = args.retrieval_backend
    if getattr(args, "as_of", None):
        overrides["as_of_date"] = args.as_of
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    return Settings.from_env(**overrides)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def _cmd_applicants(args: argparse.Namespace, settings: Settings) -> int:
    applications = list_applications(settings)
    if not applications:
        print(f"no applicants found in {settings.applicants_dir}")
        return 1
    print(f"{len(applications)} seeded applicant(s) in {settings.applicants_dir}\n")
    for application in applications:
        latest = application.latest_statement
        print(f"  {application.applicant_id}")
        print(f"    {application.legal_name} — {application.industry}, {application.country}")
        print(
            f"    requests {format_currency(application.requested_limit, application.currency)} "
            f"on net {application.requested_terms_days}; "
            f"{latest.period_label} revenue "
            f"{format_currency(latest.income_statement.revenue, application.currency)}, "
            f"{latest.opinion.value.replace('_', ' ')}"
        )
    return 0


def _cmd_seed(args: argparse.Namespace, settings: Settings) -> int:
    documents = load_corpus(settings.corpus_path)
    index = build_index(
        documents,
        backend=settings.retrieval_backend,
        dim=settings.retrieval_embedding_dim,
    )
    by_type: dict[str, int] = {}
    for document in documents:
        by_type[document.doc_type] = by_type.get(document.doc_type, 0) + 1

    print(f"corpus:            {settings.corpus_path}")
    print(f"documents indexed: {index.count()} ({corpus_hash(documents)})")
    print(f"backend:           {index.backend}")
    print("by document type:")
    for doc_type, count in sorted(by_type.items()):
        print(f"  {doc_type:18s} {count}")
    print(f"\napplicants:        {len(list_applications(settings))}")
    print(f"runs directory:    {settings.runs_dir}")
    print(f"memo directory:    {settings.memo_dir}")
    return 0


def _cmd_underwrite(args: argparse.Namespace, settings: Settings) -> int:
    application = get_application(args.applicant_id, settings)
    record = underwrite(application, settings=settings)
    out_dir = args.out or settings.memo_dir
    path = _write_memo(record.memo_markdown, out_dir, application.applicant_id)

    if args.json:
        print(json.dumps(record.decision.model_dump(mode="json"), indent=2))
    else:
        _print_summary(record)
    print(f"\nmemo written to {path}")
    print(f"run state       {settings.runs_dir / (record.run_id + '.json')}")
    if args.print_memo:
        print(f"\n{RULE}\n")
        print(record.memo_markdown)
    return 0


def _cmd_underwrite_all(args: argparse.Namespace, settings: Settings) -> int:
    applications = list_applications(settings)
    if not applications:
        print(f"no applicants found in {settings.applicants_dir}", file=sys.stderr)
        return 1

    # One context serves every applicant: the index is built once.
    context = GraphContext.build(settings)
    out_dir = args.out or settings.memo_dir
    rows: list[tuple[str, str, str, str, str]] = []

    for application in applications:
        record = underwrite(application, settings=settings, context=context)
        path = _write_memo(record.memo_markdown, out_dir, application.applicant_id)
        print(f"{RULE}")
        _print_summary(record)
        print(f"memo written to {path}")
        decision = record.decision
        rows.append(
            (
                application.legal_name,
                decision.recommendation.label,
                format_currency(decision.approved_limit, application.currency),
                f"net {decision.approved_terms_days}" if decision.approved_terms_days else "—",
                f"{decision.final_grade} ({decision.final_band_label})",
            )
        )

    print(f"\n{RULE}")
    print(f"{'applicant':32s} {'recommendation':24s} {'limit':>10s} {'terms':>8s}  grade")
    for name, recommendation, limit, terms, grade in rows:
        print(f"{name[:32]:32s} {recommendation:24s} {limit:>10s} {terms:>8s}  {grade}")
    return 0


def _cmd_runs(args: argparse.Namespace, settings: Settings) -> int:
    records = list_runs(settings)
    if not records:
        print(f"no persisted runs in {settings.runs_dir}")
        return 0
    print(f"{'run id':40s} {'created':22s} {'recommendation':24s} grade")
    for record in records:
        print(
            f"{record.run_id:40s} {record.created_at:22s} "
            f"{record.decision.recommendation.label:24s} {record.decision.final_grade}"
        )
    return 0


def _cmd_show(args: argparse.Namespace, settings: Settings) -> int:
    record = replay(args.run_id, settings)
    print(record.memo_markdown)
    return 0


def _cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    matches, stored, fresh = verify(args.run_id, settings)
    print(f"run              {stored.run_id}")
    print(f"recorded at      {stored.created_at}")
    print(f"stored hash      {stored.state_hash}")
    print(f"recomputed hash  {fresh.state_hash}")
    print(f"fingerprint      {stored.fingerprint.digest()} / {fresh.fingerprint.digest()}")
    if matches:
        print("\nreproducible: the re-run produced byte-identical analysis, decision, and memo.")
        return 0
    print(
        "\nNOT reproducible: the re-run differs. Compare the fingerprints above — a changed "
        "engine version, corpus hash, or provider explains the difference.",
        file=sys.stderr,
    )
    return 1


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    port = args.port or settings.api_port or DEFAULT_API_PORT
    print(f"serving the credit underwriter API on http://{args.host}:{port}")
    print(f"interactive docs at http://{args.host}:{port}/docs")
    uvicorn.run(
        "credit_underwriter.api:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


# --------------------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------------------


def _write_memo(markdown: str, out_dir: Path, applicant_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{applicant_id}.md"
    path.write_text(markdown)
    return path


def _print_summary(record) -> None:
    decision = record.decision
    currency = record.application.currency
    print(f"\n{record.application.legal_name}  [{record.run_id}]")
    print(f"  recommendation   {decision.recommendation.label}")
    print(
        f"  limit            {format_currency(decision.approved_limit, currency)} "
        f"of {format_currency(decision.requested_limit, currency)} requested"
    )
    print(
        f"  terms            "
        + (
            f"net {decision.approved_terms_days} of net {decision.requested_terms_days} requested"
            if decision.approved_terms_days
            else "no open account"
        )
    )
    print(
        f"  internal grade   {decision.standalone_grade} standalone → "
        f"{decision.final_grade} final ({decision.final_band_label}), "
        f"{decision.applied_notches:+.2f} notches"
    )
    print(
        f"  external risk    worst severity {record.risk_assessment.overall_severity.value}, "
        f"{len(record.risk_assessment.retrieved)} documents reviewed, "
        f"{len(record.risk_assessment.adverse_findings)} adverse findings"
    )
    print(f"  resolution rules {', '.join(c.rule for c in decision.conflicts)}")
    if decision.mitigant_offsets:
        print(
            f"  mitigant credit  "
            + ", ".join(
                f"{o.enhancement_key} {o.notch_credit:+.2f}" for o in decision.mitigant_offsets
            )
        )
    print(
        f"  memo             {len(record.memo.claims)} cited claims, revision "
        f"{record.memo.revision}, completeness check "
        + ("passed" if record.critique.passed else f"{len(record.critique.issues)} issue(s)")
    )
    print(f"  conditions       {len(decision.conditions)}")


if __name__ == "__main__":
    raise SystemExit(main())
