"""Director page and the same summary as plain text."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from o2c_control_tower.cash_forecast import (
    ON_TIME_RATE,
    PAST_DUE_CURVE,
    PROMISE_CONFIDENCE_BROKEN,
    PROMISE_CONFIDENCE_CLEAN,
)
from o2c_control_tower.format import (
    MONTHS,
    format_days,
    format_money,
    format_percent,
    long_date,
    quantize_ratio,
)
from o2c_control_tower.summary import ControlTower

_CSS = """
:root {
  --ink: #241c16;
  --muted: #6d6256;
  --paper: #f7f3ec;
  --sheet: #fffdf9;
  --line: #e3d9cc;
  --copper: #8d4324;
  --copper-soft: #f3e4d8;
  --good: #1d6a45;
  --bad: #8d2f39;
  --current: #d9cfc0;
  --b30: #e0b07a;
  --b60: #c47a45;
  --b90: #a34432;
  --b91: #6f2430;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.45 "Avenir Next", "Segoe UI", Helvetica, sans-serif;
  font-variant-numeric: tabular-nums;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }
header.top {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-end;
  border-bottom: 1px solid var(--ink);
  padding-bottom: 16px;
}
.eyebrow {
  margin: 0 0 4px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-size: 12px;
  color: var(--copper);
}
h1 { margin: 0; font-family: Palatino, "Palatino Linotype", Georgia, serif; font-weight: 600; font-size: 40px; letter-spacing: -0.03em; }
.asof { margin: 0; color: var(--muted); }
.narrative {
  font-family: Palatino, "Palatino Linotype", Georgia, serif;
  font-size: 21px;
  line-height: 1.45;
  margin: 22px 0 8px;
}
.note { color: var(--muted); font-size: 13px; margin: 0 0 22px; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 28px; }
.kpi { background: var(--sheet); border: 1px solid var(--line); padding: 14px 14px 12px; }
.kpi span { display: block; color: var(--muted); font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; }
.kpi strong { display: block; margin-top: 6px; font-size: 26px; font-weight: 650; letter-spacing: -0.03em; }
.kpi em { display: block; margin-top: 4px; font-style: normal; color: var(--muted); font-size: 13px; }
section { margin-top: 28px; }
h2 {
  margin: 0 0 12px;
  font-size: 13px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  font-weight: 650;
}
.grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 18px; }
.panel { background: var(--sheet); border: 1px solid var(--line); padding: 16px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 7px 8px 7px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
td.num, th.num { text-align: right; padding-right: 0; }
.stack { display: flex; height: 18px; width: 100%; border-radius: 2px; overflow: hidden; margin: 8px 0 12px; }
.legend { display: flex; flex-wrap: wrap; gap: 10px 16px; font-size: 13px; color: var(--muted); }
.swatch { display: inline-block; width: 10px; height: 10px; margin-right: 6px; vertical-align: -1px; }
.pos { color: var(--bad); }
.neg { color: var(--good); }
.barline { display: grid; grid-template-columns: 92px 1fr 110px; gap: 8px; align-items: center; margin: 4px 0; font-size: 14px; }
.track { background: #f0e9df; height: 10px; }
.fill { background: var(--copper); height: 10px; }
.zero .fill { width: 0; }
.reason { color: var(--muted); font-size: 13px; }
.age { font-weight: 650; }
footer { margin-top: 36px; color: var(--muted); font-size: 13px; }
a { color: var(--copper); }
@media (max-width: 800px) {
  h1 { font-size: 32px; }
  .narrative { font-size: 18px; }
  .kpis, .grid { grid-template-columns: 1fr; }
  header.top { flex-direction: column; align-items: flex-start; }
  .barline { grid-template-columns: 78px 1fr 92px; }
}
"""


def _due_label(days_past_due: int) -> str:
    if days_past_due > 0:
        unit = "day" if days_past_due == 1 else "days"
        return f"{days_past_due} {unit} past due"
    if days_past_due == 0:
        return "Due today"
    ahead = -days_past_due
    unit = "day" if ahead == 1 else "days"
    return f"Due in {ahead} {unit}"


def _signed_days(value) -> str:
    rendered = format_days(value, 4)
    if rendered.startswith("-"):
        return rendered
    if Decimal(rendered) == 0:
        return rendered
    return f"+{rendered}"


def render_text(tower: ControlTower) -> str:
    lines = [
        "O2C CONTROL TOWER",
        f"As of {long_date(tower.as_of)}",
        "",
        tower.narrative,
        "",
        f"Open AR            {format_money(tower.open_ar)}",
        f"Beginning DSO      {format_days(tower.dso.beginning_dso)} days",
        f"Ending DSO         {format_days(tower.dso.ending_dso)} days",
        f"Predicted cash     {format_money(tower.cash.total)}   {tower.cash.start.isoformat()} to {tower.cash.end.isoformat()}",
        f"Waiting actions    {len(tower.actions)}",
        f"Unapplied cash     {format_money(tower.unapplied_cash)}  (not in AR, not in the forecast)",
        "",
        "AGING",
    ]
    for bucket in tower.aging.buckets:
        share = format_percent(bucket.amount / tower.aging.total)
        lines.append(
            f"  {bucket.label:<10} {format_money(bucket.amount):>14}   {bucket.invoice_count:>2}   {share:>6}"
        )
    lines.append(f"  {'Total':<10} {format_money(tower.aging.total):>14}   {len(tower.aging.invoices):>2}")
    dso = tower.dso
    lines.extend(
        [
            "",
            f"DSO BRIDGE  ({dso.prior.end.isoformat()} to {dso.current.end.isoformat()}, {dso.days} days)",
            f"  Beginning AR     {format_money(dso.beginning_ar):>14}   sales {format_money(dso.prior_sales)}",
            f"  Billings         {format_money(dso.billings):>14}",
            f"  Collections      {format_money(-dso.collections):>14}",
            f"  Credit memos     {format_money(-dso.credit_memos):>14}",
            f"  Other            {format_money(dso.other):>14}",
            f"  Ending AR        {format_money(dso.ending_ar):>14}   sales {format_money(dso.current_sales)}",
            f"  Beginning DSO    {format_days(dso.beginning_dso):>10}",
        ]
    )
    running = quantize_ratio(dso.beginning_dso)
    for _key, label, effect in dso.effects():
        running += quantize_ratio(effect)
        lines.append(f"  {label:<16} {_signed_days(effect):>10}   running {running:.4f}")
    lines.append(f"  {'Ending DSO':<16} {format_days(dso.ending_dso):>10}")
    lines.extend(["", "CASH FORECAST"])
    for day in tower.cash.days:
        lines.append(f"  {day.day.isoformat()}   {format_money(day.amount):>12}")
    lines.extend(["", "COLLECTORS", "  Score = open invoices + 2×past-due invoices + 3×broken promises + waiting actions"])
    for load in tower.collectors:
        customers = ", ".join(load.customers)
        lines.append(
            f"  {load.collector:<16} score {load.workload_score:<3}  "
            f"open {format_money(load.open_ar):>12}  "
            f"past due {format_money(load.past_due_ar):>12}  "
            f"broken {load.broken_promises}  queue {load.waiting_actions}"
        )
        lines.append(f"    {customers}")
    if tower.outside_collectors:
        lines.append(f"  Also waiting on {', '.join(tower.outside_collectors)}, outside the collector books.")
    lines.extend(["", "WAITING ON A HUMAN"])
    for action in tower.actions:
        invoice = action.invoice_id or "—"
        lines.append(
            f"  {action.age_days:>2}d  {action.label:<16} {action.owner:<14} "
            f"{action.customer_name:<24} {invoice:<10} {format_money(action.amount):>12}"
        )
        lines.append(f"      {action.reason}")
    lines.append("")
    return "\n".join(lines)


def render_html(tower: ControlTower) -> str:
    total = tower.aging.total
    colors = {
        "current": "var(--current)",
        "1_30": "var(--b30)",
        "31_60": "var(--b60)",
        "61_90": "var(--b90)",
        "91_plus": "var(--b91)",
    }
    segments = []
    legend = []
    for bucket in tower.aging.buckets:
        share = format_percent(bucket.amount / total) if total else "0.0%"
        segments.append(
            f'<div title="{escape(bucket.label)} {escape(format_money(bucket.amount))}" '
            f'style="flex:{int(bucket.amount)} 1 0;background:{colors[bucket.key]}"></div>'
        )
        legend.append(
            f'<span><i class="swatch" style="background:{colors[bucket.key]}"></i>'
            f"{escape(bucket.label)} {escape(share)}</span>"
        )
    aging_rows = []
    for bucket in tower.aging.buckets:
        aging_rows.append(
            "<tr>"
            f"<td>{escape(bucket.label)}</td>"
            f'<td class="num">{bucket.invoice_count}</td>'
            f'<td class="num">{escape(format_money(bucket.amount))}</td>'
            f'<td class="num">{escape(format_percent(bucket.amount / total))}</td>'
            "</tr>"
        )
    dso = tower.dso
    running = quantize_ratio(dso.beginning_dso)
    bridge_rows = [
        "<tr>"
        f"<td>Beginning DSO</td>"
        f'<td class="num">{escape(format_days(dso.beginning_dso))}</td>'
        f'<td class="num">{escape(format_days(dso.beginning_dso))}</td>'
        "</tr>"
    ]
    for _key, label, effect in dso.effects():
        rounded = quantize_ratio(effect)
        running += rounded
        css = "pos" if rounded > 0 else "neg" if rounded < 0 else ""
        bridge_rows.append(
            "<tr>"
            f"<td>{escape(label)}</td>"
            f'<td class="num {css}">{escape(_signed_days(effect))}</td>'
            f'<td class="num">{running:.4f}</td>'
            "</tr>"
        )
    bridge_rows.append(
        "<tr>"
        "<td>Ending DSO</td>"
        f'<td class="num">{escape(format_days(dso.ending_dso))}</td>'
        f'<td class="num">{escape(format_days(dso.ending_dso))}</td>'
        "</tr>"
    )
    peak = max((day.amount for day in tower.cash.days), default=Decimal("0"))
    cash_rows = []
    for day in tower.cash.days:
        width = Decimal("0") if peak == 0 else (day.amount / peak * Decimal(100))
        when = day.day
        cash_rows.append(
            f'<div class="barline{" zero" if day.amount == 0 else ""}">'
            f"<span>{when.day:02d} {escape(MONTHS[when.month - 1][:3])}</span>"
            f'<div class="track"><div class="fill" style="width:{width:.1f}%"></div></div>'
            f'<span class="num">{escape(format_money(day.amount))}</span>'
            "</div>"
        )
    curve = ", ".join(
        f"{label} {int(rate * 100)}% on day {offset}"
        for label, (rate, offset) in (
            ("1–30", PAST_DUE_CURVE["1_30"]),
            ("31–60", PAST_DUE_CURVE["31_60"]),
            ("61–90", PAST_DUE_CURVE["61_90"]),
            ("91+", PAST_DUE_CURVE["91_plus"]),
        )
    )
    collector_rows = []
    for load in tower.collectors:
        collector_rows.append(
            "<tr>"
            f"<td>{escape(load.collector)}<div class='reason'>{escape(', '.join(load.customers))}</div></td>"
            f'<td class="num">{escape(format_money(load.open_ar))}<div class="reason">{load.open_invoices} open</div></td>'
            f'<td class="num">{escape(format_money(load.past_due_ar))}<div class="reason">{load.past_due_invoices} past due</div></td>'
            f'<td class="num">{load.broken_promises}</td>'
            f'<td class="num">{load.waiting_actions}</td>'
            f'<td class="num">{load.workload_score}</td>'
            "</tr>"
        )
    outside = ""
    if tower.outside_collectors:
        names = ", ".join(tower.outside_collectors)
        outside = f'<p class="reason">Also waiting on {escape(names)}, who is not a collector on this book.</p>'
    action_rows = []
    for action in tower.actions:
        invoice = action.invoice_id or "Customer"
        unit = "day" if action.age_days == 1 else "days"
        action_rows.append(
            "<tr>"
            f'<td class="age">{action.age_days} {unit}</td>'
            f"<td>{escape(action.label)}<div class='reason'>{escape(invoice)}</div></td>"
            f"<td>{escape(action.customer_name)}</td>"
            f'<td class="num">{escape(format_money(action.amount))}</td>'
            f"<td>{escape(action.owner)}</td>"
            f"<td class='reason'>{escape(action.reason)}</td>"
            "</tr>"
        )
    invoice_rows = []
    for row in tower.aging.invoices:
        invoice_rows.append(
            "<tr>"
            f"<td>{escape(row.invoice_id)}</td>"
            f"<td>{escape(row.customer_name)}</td>"
            f"<td>{escape(row.collector)}</td>"
            f"<td>{escape(row.due_on)}</td>"
            f"<td>{escape(_due_label(row.days_past_due))}</td>"
            f'<td class="num">{escape(format_money(row.open_amount))}</td>'
            "</tr>"
        )
    change = quantize_ratio(dso.ending_dso) - quantize_ratio(dso.beginning_dso)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>O2C control tower</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <div>
        <p class="eyebrow">Accounts receivable</p>
        <h1>Control tower</h1>
      </div>
      <p class="asof">As of {escape(long_date(tower.as_of))}</p>
    </header>
    <p class="narrative">{escape(tower.narrative)}</p>
    <p class="note">Figures are computed from the seeded book. This paragraph only phrases them. JSON for the same snapshot is at <a href="/api/summary">/api/summary</a>.</p>
    <div class="kpis">
      <div class="kpi"><span>Open AR</span><strong>{escape(format_money(tower.open_ar))}</strong><em>{len(tower.aging.invoices)} open invoices</em></div>
      <div class="kpi"><span>DSO, 30 days</span><strong>{escape(format_days(dso.ending_dso, 2))}</strong><em>from {escape(format_days(dso.beginning_dso, 2))}, {change:+.2f} days</em></div>
      <div class="kpi"><span>Cash, next 14 days</span><strong>{escape(format_money(tower.cash.total))}</strong><em>{escape(tower.cash.start.isoformat())} to {escape(tower.cash.end.isoformat())}</em></div>
      <div class="kpi"><span>Waiting on a person</span><strong>{len(tower.actions)}</strong><em>Unapplied cash {escape(format_money(tower.unapplied_cash))}</em></div>
    </div>
    <div class="grid">
      <section class="panel">
        <h2>Aging</h2>
        <div class="stack">{''.join(segments)}</div>
        <div class="legend">{''.join(legend)}</div>
        <table>
          <thead><tr><th>Bucket</th><th class="num">Invoices</th><th class="num">Open</th><th class="num">Share</th></tr></thead>
          <tbody>
            {''.join(aging_rows)}
            <tr><td>Total</td><td class="num">{len(tower.aging.invoices)}</td><td class="num">{escape(format_money(total))}</td><td class="num">100%</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>What moved DSO</h2>
        <table>
          <tbody>
            <tr><td>Beginning AR, {escape(long_date(dso.prior.end))}</td><td class="num">{escape(format_money(dso.beginning_ar))}</td></tr>
            <tr><td>Billings</td><td class="num">{escape(format_money(dso.billings))}</td></tr>
            <tr><td>Collections</td><td class="num neg">{escape(format_money(-dso.collections))}</td></tr>
            <tr><td>Posted credit memos</td><td class="num neg">{escape(format_money(-dso.credit_memos))}</td></tr>
            <tr><td>Ending AR, {escape(long_date(dso.current.end))}</td><td class="num">{escape(format_money(dso.ending_ar))}</td></tr>
            <tr><td>Prior-window sales</td><td class="num">{escape(format_money(dso.prior_sales))}</td></tr>
            <tr><td>Current-window sales</td><td class="num">{escape(format_money(dso.current_sales))}</td></tr>
          </tbody>
        </table>
        <table>
          <thead><tr><th>Bridge</th><th class="num">Days</th><th class="num">Running</th></tr></thead>
          <tbody>{''.join(bridge_rows)}</tbody>
        </table>
        <p class="reason">DSO = open AR × 30 / gross billings. The bridge reprices beginning AR on current sales, then applies billings, collections, and posted credit memos on that same base. Rounded days tie to ending DSO.</p>
      </section>
    </div>
    <section class="panel">
      <h2>Predicted cash</h2>
      {''.join(cash_rows)}
      <p class="reason">Promises at {int(PROMISE_CONFIDENCE_CLEAN * 100)}% confidence, or {int(PROMISE_CONFIDENCE_BROKEN * 100)}% after a broken promise. Invoices due inside the window with no promise at {int(ON_TIME_RATE * 100)}%. Past due: {escape(curve)}. One path per invoice. Unapplied cash is already in the bank.</p>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Collector workload</h2>
        <table>
          <thead><tr><th>Collector</th><th class="num">Open AR</th><th class="num">Past due</th><th class="num">Broken</th><th class="num">Queue</th><th class="num">Score</th></tr></thead>
          <tbody>{''.join(collector_rows)}</tbody>
        </table>
        <p class="reason">Score = open invoices + 2 × past-due invoices + 3 × broken promises + actions that collector owns.</p>
        {outside}
      </section>
      <section class="panel">
        <h2>Waiting on a human</h2>
        <table>
          <thead><tr><th>Age</th><th>Action</th><th>Customer</th><th class="num">Amount</th><th>Owner</th><th>Why it stopped</th></tr></thead>
          <tbody>{''.join(action_rows)}</tbody>
        </table>
      </section>
    </div>
    <section class="panel">
      <h2>Open invoices</h2>
      <table>
        <thead><tr><th>Invoice</th><th>Customer</th><th>Collector</th><th>Due</th><th>Status</th><th class="num">Open</th></tr></thead>
        <tbody>{''.join(invoice_rows)}</tbody>
      </table>
    </section>
    <footer>Order-to-cash control tower. Seeded portfolio, as of {escape(tower.as_of.isoformat())}. Pending credit memos and unmatched receipts are not in the AR rollforward.</footer>
  </div>
</body>
</html>
"""
