"""Clerk desk. Server-rendered, no build step."""

from __future__ import annotations

from html import escape
from typing import Any

QUEUE_ORDER = ["warehouse", "sales", "sales_coop", "returns", "quality", "recovery"]

FACT_LABELS = {
    "sku": "SKU",
    "unit_price": "Unit price",
    "invoice_date": "Invoice date",
    "delivery_date": "Delivery date",
    "ship_date": "Ship date",
    "invoiced_qty": "Invoiced quantity",
    "pod_qty": "POD quantity",
    "claimed_units": "Claimed units",
    "claimed_rate": "Claimed rate",
    "promo_used": "Promo already claimed",
    "rma_number": "RMA",
    "rma_authorized": "RMA authorized",
    "rma_qty": "RMA quantity",
    "return_qty": "Returned quantity",
    "features_our_brand": "Ad features our brand",
    "proof_submitted": "Proof submitted",
    "ad_run_date": "Ad run date",
    "proof_date": "Proof date",
    "accrual_balance": "Accrual balance",
    "damaged_qty": "Damaged quantity",
    "damage_evidence": "Damage evidence",
}

MONEY_FACTS = {"unit_price", "claimed_rate", "promo_used", "accrual_balance"}


def page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --paper: #f3efe6;
      --card: #faf7f1;
      --ink: #1c1915;
      --muted: #5e564c;
      --line: #ddd4c6;
      --hold: #8c3a2f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font: 16px/1.45 "Segoe UI", "Helvetica Neue", sans-serif;
    }}
    header {{
      border-bottom: 1px solid var(--ink);
      padding: 22px 28px 18px;
    }}
    header a {{ color: inherit; text-decoration: none; }}
    .kicker {{
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--muted);
      margin: 0 0 6px;
    }}
    h1 {{ font: 34px/1.1 Georgia, "Iowan Old Style", Palatino, serif; margin: 0; }}
    .lede {{ margin: 8px 0 0; max-width: 46rem; color: var(--muted); }}
    main {{ padding: 22px 28px 48px; max-width: 1180px; }}
    .stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 18px; }}
    .stat {{
      background: var(--card);
      border: 1px solid var(--line);
      padding: 10px 14px;
      min-width: 140px;
    }}
    .stat b {{ display: block; font: 22px/1.1 Georgia, Palatino, serif; }}
    .stat span {{ color: var(--muted); font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); font-weight: 600; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    a.case {{ color: var(--ink); font-weight: 650; }}
    .chip {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      color: white;
      font-size: 13px;
      white-space: nowrap;
    }}
    .q-warehouse {{ background: #1e4d7b; }}
    .q-sales {{ background: #1d6b45; }}
    .q-sales_coop {{ background: #0e6b62; }}
    .q-returns {{ background: #8a5a24; }}
    .q-quality {{ background: #6d3a78; }}
    .q-recovery {{ background: #8c3a2f; }}
    .outcome-valid {{ color: #1d6b45; font-weight: 650; }}
    .outcome-invalid {{ color: var(--hold); font-weight: 650; }}
    .layout {{ display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 18px; }}
    article, form, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      padding: 16px 18px;
      margin: 0 0 14px;
    }}
    h2 {{ font: 22px/1.2 Georgia, Palatino, serif; margin: 0 0 8px; }}
    h3 {{ font-size: 14px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin: 0 0 8px; }}
    pre {{
      white-space: pre-wrap;
      font: 14px/1.45 ui-monospace, "Cascadia Code", monospace;
      margin: 0;
    }}
    blockquote {{
      margin: 0;
      border-left: 3px solid var(--hold);
      padding: 4px 0 4px 12px;
    }}
    ul.checks {{ list-style: none; padding: 0; margin: 0; }}
    ul.checks li {{ padding: 8px 0; border-bottom: 1px solid var(--line); }}
    ul.checks li:last-child {{ border-bottom: 0; }}
    .pass {{ color: #1d6b45; }}
    .fail {{ color: var(--hold); }}
    label {{ display: block; font-size: 13px; margin: 10px 0 4px; }}
    input, textarea, select {{
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      font: inherit;
    }}
    button {{
      margin-top: 14px;
      background: var(--ink);
      color: var(--paper);
      border: 0;
      padding: 10px 16px;
      font: inherit;
      cursor: pointer;
    }}
    .hold {{ color: var(--hold); }}
    .banner {{
      border: 1px solid var(--ink);
      padding: 12px 14px;
      margin: 0 0 14px;
      background: white;
    }}
    .muted {{ color: var(--muted); }}
    dl {{ display: grid; grid-template-columns: 180px 1fr; gap: 4px 12px; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    @media (max-width: 860px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .layout {{ grid-template-columns: 1fr; }}
      dl {{ grid-template-columns: 1fr; }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <p class="kicker">Harbor &amp; Field · order to cash</p>
    <h1><a href="/">Deduction Workbench</a></h1>
    <p class="lede">Customer deductions are classified, checked against the contract or promotion, and held for a clerk. Confirming a route does not email the customer or post a credit.</p>
  </header>
  <main>
    {body}
  </main>
</body>
</html>"""


def render_queue(cases: list[dict[str, Any]], sent: int) -> str:
    waiting = sum(1 for case in cases if case["status"] == "awaiting_confirmation")
    confirmed = sum(1 for case in cases if case["status"] == "confirmed")
    if not cases:
        body = """
        <div class="banner">
          <h2>No deductions on the desk</h2>
          <p>Seed the workbench, then refresh. New backups are classified before they show up here.</p>
        </div>
        """
        return page("Deduction Workbench", body)
    rows = "\n".join(_row(case) for case in cases)
    legend = " ".join(_chip(queue, _label(queue)) for queue in QUEUE_ORDER)
    body = f"""
    <div class="stats">
      <div class="stat"><b>{waiting}</b><span>waiting for a clerk</span></div>
      <div class="stat"><b>{confirmed}</b><span>confirmed</span></div>
      <div class="stat"><b>{sent}</b><span>sent</span></div>
    </div>
    <p class="muted">Desks: {legend}</p>
    <table>
      <thead>
        <tr>
          <th>Case</th><th>Customer</th><th>Debit</th>
          <th class="num">Amount</th><th>Reason</th><th>Outcome</th>
          <th>Route</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    """
    return page("Deduction Workbench", body)


def render_case(case: dict[str, Any], error: str = "", just_confirmed: bool = False) -> str:
    banner = ""
    if error:
        banner = f'<div class="banner"><p class="hold">{escape(error)}</p></div>'
    elif just_confirmed or case["status"] == "confirmed":
        who = escape(case.get("confirmed_by") or "a clerk")
        banner = (
            f'<div class="banner"><p>Confirmed by {who}. '
            "The route is recorded. Nothing was sent.</p></div>"
        )
    outcome = case.get("outcome") or "pending"
    queue = case.get("queue") or ""
    citation = case.get("citation_text") or ""
    checks = "".join(_check(item) for item in case.get("checks") or [])
    retrieved = "".join(_retrieved(item) for item in case.get("retrieved") or [])
    facts = _facts(case.get("facts") or {})
    confirm = _confirm_form(case) if case["status"] == "awaiting_confirmation" else ""
    note = ""
    if case.get("confirmation_note"):
        note = f"<p><strong>Clerk note.</strong> {escape(case['confirmation_note'])}</p>"
    body = f"""
    {banner}
    <p><a href="/">Back to the queue</a></p>
    <div class="layout">
      <div>
        <article>
          <p class="kicker">{escape(case["id"])} · {escape(case["debit_memo"])}</p>
          <h2>{escape(case["customer_name"])}</h2>
          <p>{_money(case["claimed_amount"])} against {escape(case["invoice_number"])}, claimed {escape(case["claim_date"])}.</p>
          <p>Reason <strong>{escape(case.get("reason_label") or "—")}</strong>
             · outcome <span class="outcome-{escape(outcome)}">{escape(outcome)}</span>
             · route {_chip(queue, case.get("queue_label") or "Unrouted")}</p>
          <p>{escape(case.get("rationale") or "Not routed yet.")}</p>
        </article>
        <article>
          <h3>Clerk note</h3>
          <p>{escape(case.get("narrative") or "The narrative is written after the route is proposed.")}</p>
          <p class="muted">Narrative provider: {escape(str(case.get("llm_provider") or "—"))}. It does not choose the route.</p>
        </article>
        <article>
          <h3>Policy checks</h3>
          <ul class="checks">{checks or "<li>No checks yet.</li>"}</ul>
        </article>
        <article>
          <h3>Cited clause · {escape(case.get("citation_id") or "—")}</h3>
          <blockquote><p>{escape(citation) if citation else "No clause cited."}</p></blockquote>
        </article>
        <article>
          <h3>Retrieved passages</h3>
          {retrieved or "<p>Nothing retrieved.</p>"}
        </article>
      </div>
      <div>
        {confirm}
        {note}
        <div class="panel">
          <h3>Agreement file</h3>
          <p>{escape(", ".join(case.get("agreement_ids") or []))}</p>
          <h3>ERP facts</h3>
          {facts}
        </div>
        <div class="panel">
          <h3>Customer email</h3>
          <pre>{escape(case.get("backup_email") or "")}</pre>
        </div>
        <div class="panel">
          <h3>Debit memo</h3>
          <pre>{escape(case.get("debit_memo_text") or "")}</pre>
        </div>
      </div>
    </div>
    """
    return page(f"{case['id']} · Deduction Workbench", body)


def render_not_found(case_id: str) -> str:
    body = f"""
    <div class="banner">
      <h2>No deduction {escape(case_id)}</h2>
      <p>That case is not on this desk. <a href="/">Return to the queue</a>.</p>
    </div>
    """
    return page("Not found · Deduction Workbench", body)


def _row(case: dict[str, Any]) -> str:
    outcome = case.get("outcome") or "—"
    return (
        "<tr>"
        f'<td><a class="case" href="/cases/{escape(case["id"])}">{escape(case["id"])}</a></td>'
        f"<td>{escape(case['customer_name'])}</td>"
        f"<td>{escape(case['debit_memo'])}</td>"
        f"<td class=\"num\">{_money(case['claimed_amount'])}</td>"
        f"<td>{escape(case.get('reason_label') or '—')}</td>"
        f'<td class="outcome-{escape(outcome)}">{escape(outcome)}</td>'
        f"<td>{_chip(case.get('queue') or '', case.get('queue_label') or '—')}</td>"
        f"<td>{escape(_status(case['status']))}</td>"
        "</tr>"
    )


def _confirm_form(case: dict[str, Any]) -> str:
    options = []
    current = case.get("proposed_queue") or case.get("queue")
    for queue in QUEUE_ORDER:
        selected = " selected" if queue == current else ""
        options.append(f'<option value="{escape(queue)}"{selected}>{escape(_label(queue))}</option>')
    return f"""
    <form method="post" action="/cases/{escape(case["id"])}/confirm" id="confirm-form">
      <h2>Confirm the route</h2>
      <p class="hold">This records your decision. It does not email the customer, post a credit, or release the deduction.</p>
      <label for="clerk_id">Clerk id</label>
      <input id="clerk_id" name="clerk_id" required maxlength="80" placeholder="Your clerk id" autocomplete="username">
      <label for="queue">Route</label>
      <select id="queue" name="queue">{"".join(options)}</select>
      <label for="note">Note</label>
      <textarea id="note" name="note" rows="3" maxlength="500" placeholder="Why you are confirming or redirecting this"></textarea>
      <button type="submit">Confirm route</button>
    </form>
    """


def _check(item: dict[str, Any]) -> str:
    passed = bool(item.get("passed"))
    mark = "pass" if passed else "fail"
    word = "Pass" if passed else "Fail"
    return (
        f'<li><strong class="{mark}">{word}</strong> · {escape(str(item.get("name", "")))}'
        f'<br>{escape(str(item.get("detail", "")))}</li>'
    )


def _retrieved(item: dict[str, Any]) -> str:
    return (
        "<p><strong>"
        + escape(str(item.get("clause_id", "")))
        + "</strong> · score "
        + escape(str(item.get("score", "")))
        + "<br><span class=\"muted\">"
        + escape(str(item.get("excerpt", "")))
        + "</span></p>"
    )


def _facts(facts: dict[str, Any]) -> str:
    rows = []
    for key, value in facts.items():
        if value is None or value == "":
            continue
        label = FACT_LABELS.get(key, key.replace("_", " "))
        if key in MONEY_FACTS and isinstance(value, (int, float)):
            shown = _money(float(value))
        elif isinstance(value, bool):
            shown = "yes" if value else "no"
        else:
            shown = str(value)
        rows.append(f"<dt>{escape(label)}</dt><dd>{escape(shown)}</dd>")
    if not rows:
        return "<p>No facts on file.</p>"
    return "<dl>" + "".join(rows) + "</dl>"


def _chip(queue: str, label: str) -> str:
    css = f"q-{queue}" if queue in QUEUE_ORDER else "q-recovery"
    return f'<span class="chip {css}">{escape(label)}</span>'


def _label(queue: str) -> str:
    return {
        "warehouse": "Warehouse",
        "sales": "Sales",
        "sales_coop": "Sales co-op",
        "returns": "Returns",
        "quality": "Quality",
        "recovery": "Recovery",
    }.get(queue, queue or "Unrouted")


def _status(status: str) -> str:
    return {
        "new": "New",
        "awaiting_confirmation": "Awaiting confirmation",
        "confirmed": "Confirmed",
    }.get(status, status)


def _money(amount: float) -> str:
    return f"${amount:,.2f}"
