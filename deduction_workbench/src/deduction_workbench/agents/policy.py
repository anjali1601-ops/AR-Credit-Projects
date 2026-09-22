"""Check a classified deduction against the customer's agreements.

Every pass or fail is a named check over figures in the case file and terms
loaded from the corpus. A model never decides validity.
"""

from __future__ import annotations

from datetime import date

from deduction_workbench.corpus.loader import Clause, Corpus, load_corpus
from deduction_workbench.models import CaseRecord, Check, PolicyResult, RetrievedClause

_QUERIES = {
    "shortage": "shortage short-ship proof of delivery pod quantity missing cases claim window",
    "pricing": "invoice price promotional bill-back scan allowance authorized rate",
    "returns": "return RMA authorization returned goods restock window",
    "coop_advertising": "co-op advertising tear sheet proof of performance accrual trade funds",
    "damaged_goods": "damaged goods concealed damage crushed carrier notice window",
}


def evaluate(reason_code: str, case: CaseRecord, corpus: Corpus | None = None) -> PolicyResult:
    library = corpus or load_corpus()
    clauses = library.for_agreements(case.agreement_ids)
    query = _query(reason_code, case)
    retrieved_pairs = library.retrieve(query, case.agreement_ids, reason_code, k=6)
    retrieved = [_hit(clause, score) for clause, score in retrieved_pairs]
    if reason_code == "shortage":
        result = _shortage(case, clauses)
    elif reason_code == "pricing":
        result = _pricing(case, clauses)
    elif reason_code == "returns":
        result = _returns(case, clauses)
    elif reason_code == "coop_advertising":
        result = _coop(case, clauses)
    elif reason_code == "damaged_goods":
        result = _damage(case, clauses)
    else:
        fallback = _one(clauses, "trade_funds") or (clauses[0] if clauses else None)
        if fallback is None:
            raise RuntimeError(f"{case.id} has no agreements to cite")
        result = _invalid(
            fallback,
            [Check("recognized_reason", False, "The backup did not match a known reason code.")],
        )
    result.retrieved = retrieved
    return result


def _query(reason_code: str, case: CaseRecord) -> str:
    sku = str(case.facts.get("sku") or "")
    base = _QUERIES.get(reason_code, reason_code)
    return f"{base} {sku}\n{case.backup_email}\n{case.debit_memo_text}"


def _hit(clause: Clause, score: int) -> RetrievedClause:
    excerpt = clause.text if len(clause.text) <= 280 else clause.text[:277].rstrip() + "..."
    return RetrievedClause(
        clause_id=clause.clause_id,
        agreement_id=clause.agreement_id,
        heading=clause.heading,
        kind=clause.kind,
        score=score,
        excerpt=excerpt,
    )


def _shortage(case: CaseRecord, clauses: list[Clause]) -> PolicyResult:
    clause = _require(clauses, "shortage", case.id)
    window = int(clause.terms["claim_window_days"])
    invoiced = _num(case.facts.get("invoiced_qty"))
    pod = _num(case.facts.get("pod_qty"))
    price = _num(case.facts.get("unit_price"))
    delivery = case.facts.get("delivery_date")
    checks: list[Check] = []
    if invoiced is None or pod is None:
        checks.append(Check("pod_shows_shortage", False, "Invoiced quantity or POD quantity is not on file."))
        short_qty = None
    elif pod < invoiced:
        short_qty = invoiced - pod
        checks.append(
            Check(
                "pod_shows_shortage",
                True,
                f"POD { _qty(pod) } is below invoiced { _qty(invoiced) } (short { _qty(short_qty) }).",
            )
        )
    else:
        short_qty = 0
        checks.append(
            Check(
                "pod_shows_shortage",
                False,
                f"POD quantity { _qty(pod) } equals or exceeds invoiced quantity { _qty(invoiced) }, so our records do not show a shortage.",
            )
        )
    if short_qty and price is not None:
        supported = short_qty * price
        matches = _same_money(supported, case.claimed_amount)
        checks.append(
            Check(
                "amount_matches_short_units",
                matches,
                f"Claimed { _money(case.claimed_amount) } against { _qty(short_qty) } short × { _money(price) } = { _money(supported) }.",
            )
        )
    else:
        checks.append(
            Check(
                "amount_matches_short_units",
                False,
                "There is no supported short quantity at the invoice price to compare with the debit.",
            )
        )
    checks.append(_window_check("inside_claim_window", delivery, case.claim_date, window, "delivery"))
    return _from_checks(clause, checks)


def _pricing(case: CaseRecord, clauses: list[Clause]) -> PolicyResult:
    master = _require(clauses, "pricing_default", case.id)
    sku = case.facts.get("sku")
    ship = case.facts.get("ship_date")
    promos = [clause for clause in clauses if clause.kind == "pricing_promo"]
    match = None
    for promo in promos:
        if str(promo.terms.get("sku")) != str(sku):
            continue
        start = str(promo.terms.get("start"))
        end = str(promo.terms.get("end"))
        if ship and start <= str(ship) <= end:
            match = promo
            break
    if match is None:
        covered = ", ".join(sorted({str(promo.terms.get("sku")) for promo in promos})) or "none"
        return _invalid(
            master,
            [
                Check(
                    "promo_covers_sku_and_ship_date",
                    False,
                    f"No written promotion covers SKU {sku} shipped {ship}. Promotions on file: {covered}.",
                )
            ],
        )
    rate = float(match.terms["rate"])
    cap = float(match.terms["cap"])
    claimed_rate = _num(case.facts.get("claimed_rate"))
    units = _num(case.facts.get("claimed_units"))
    used = _num(case.facts.get("promo_used")) or 0.0
    checks = [
        Check(
            "promo_covers_sku_and_ship_date",
            True,
            f"{match.agreement_id} covers SKU {sku} shipped {ship} ({match.terms['start']} through {match.terms['end']}).",
        )
    ]
    if claimed_rate is None:
        checks.append(Check("rate_matches_agreement", False, "The claimed rate is not on the debit."))
    else:
        checks.append(
            Check(
                "rate_matches_agreement",
                abs(claimed_rate - rate) < 0.0001,
                f"Claimed rate { _money(claimed_rate) } against authorized { _money(rate) } per unit.",
            )
        )
    if units is None:
        checks.append(Check("amount_matches_units", False, "Claimed units are not on the debit."))
        extended = None
    else:
        extended = units * rate
        checks.append(
            Check(
                "amount_matches_units",
                _same_money(units * (claimed_rate if claimed_rate is not None else rate), case.claimed_amount)
                and claimed_rate is not None
                and abs(claimed_rate - rate) < 0.0001,
                f"Claimed { _money(case.claimed_amount) } against { _qty(units) } × { _money(rate) } = { _money(extended) }.",
            )
        )
    projected = used + case.claimed_amount
    checks.append(
        Check(
            "within_cap",
            projected <= cap + 0.001,
            f"Prior claims { _money(used) } plus this debit { _money(case.claimed_amount) } = { _money(projected) }, cap { _money(cap) }.",
        )
    )
    return _from_checks(match, checks)


def _returns(case: CaseRecord, clauses: list[Clause]) -> PolicyResult:
    clause = _require(clauses, "returns", case.id)
    window = int(clause.terms["window_days"])
    authorized = bool(case.facts.get("rma_authorized")) and bool(case.facts.get("rma_number"))
    rma_number = case.facts.get("rma_number") or "none"
    rma_qty = _num(case.facts.get("rma_qty"))
    returned = _num(case.facts.get("return_qty"))
    price = _num(case.facts.get("unit_price"))
    checks = [
        Check(
            "rma_on_file",
            authorized,
            f"RMA {rma_number} is {'on file and authorized' if authorized else 'not on file'}.",
        )
    ]
    if authorized and rma_qty is not None and returned is not None and returned <= rma_qty:
        checks.append(
            Check(
                "quantity_within_rma",
                True,
                f"Returned { _qty(returned) } does not exceed RMA quantity { _qty(rma_qty) }.",
            )
        )
    else:
        checks.append(
            Check(
                "quantity_within_rma",
                False,
                "Returned quantity is missing or higher than the authorized RMA quantity.",
            )
        )
    if returned is not None and price is not None and authorized:
        supported = returned * price
        checks.append(
            Check(
                "amount_matches_return",
                _same_money(supported, case.claimed_amount),
                f"Claimed { _money(case.claimed_amount) } against { _qty(returned) } × { _money(price) } = { _money(supported) }.",
            )
        )
    else:
        checks.append(
            Check(
                "amount_matches_return",
                False,
                "There is no authorized return quantity at the invoice price to support the debit.",
            )
        )
    checks.append(_window_check("inside_return_window", case.facts.get("invoice_date"), case.claim_date, window, "invoice"))
    return _from_checks(clause, checks)


def _coop(case: CaseRecord, clauses: list[Clause]) -> PolicyResult:
    master = _require(clauses, "trade_funds", case.id)
    agreements = [clause for clause in clauses if clause.kind == "coop"]
    if not agreements:
        return _invalid(
            master,
            [
                Check(
                    "agreement_on_file",
                    False,
                    "No separate co-op advertising agreement is in force for this customer.",
                )
            ],
        )
    clause = agreements[0]
    window = int(clause.terms["proof_window_days"])
    features = bool(case.facts.get("features_our_brand"))
    proof = bool(case.facts.get("proof_submitted"))
    accrual = _num(case.facts.get("accrual_balance"))
    checks = [
        Check("agreement_on_file", True, f"{clause.agreement_id} is the co-op agreement on file."),
        Check(
            "features_brand",
            features,
            "The backup shows the ad featured Harbor & Field." if features else "The ad did not feature Harbor & Field.",
        ),
    ]
    if not proof:
        checks.append(Check("proof_in_window", False, "Proof of performance was not submitted."))
    else:
        checks.append(
            _window_check(
                "proof_in_window",
                case.facts.get("ad_run_date"),
                str(case.facts.get("proof_date") or ""),
                window,
                "ad run",
            )
        )
    if accrual is None:
        checks.append(Check("within_accrual", False, "The accrual balance is not on file."))
    else:
        checks.append(
            Check(
                "within_accrual",
                case.claimed_amount <= accrual + 0.001,
                f"Debit { _money(case.claimed_amount) } against accrual balance { _money(accrual) }.",
            )
        )
    return _from_checks(clause, checks)


def _damage(case: CaseRecord, clauses: list[Clause]) -> PolicyResult:
    clause = _require(clauses, "damaged_goods", case.id)
    window = int(clause.terms["notice_days"])
    evidence = bool(case.facts.get("damage_evidence"))
    qty = _num(case.facts.get("damaged_qty"))
    price = _num(case.facts.get("unit_price"))
    checks = [
        Check(
            "evidence_on_file",
            evidence,
            "Photographs or a carrier exception are on file." if evidence else "No photographs or carrier exception are on file.",
        )
    ]
    checks.append(_window_check("inside_notice_window", case.facts.get("delivery_date"), case.claim_date, window, "delivery"))
    if qty is not None and price is not None and qty > 0:
        supported = qty * price
        checks.append(
            Check(
                "amount_matches_damaged_units",
                _same_money(supported, case.claimed_amount),
                f"Claimed { _money(case.claimed_amount) } against { _qty(qty) } damaged × { _money(price) } = { _money(supported) }.",
            )
        )
    else:
        checks.append(
            Check(
                "amount_matches_damaged_units",
                False,
                "Damaged quantity or invoice price is missing, so the debit is not supported.",
            )
        )
    return _from_checks(clause, checks)


def _window_check(name: str, start: object, end: object, allowed: int, start_label: str) -> Check:
    if not start or not end:
        return Check(name, False, f"The {start_label} date or the claim date is missing.")
    try:
        elapsed = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return Check(name, False, f"Could not read the {start_label} date or the claim date.")
    passed = 0 <= elapsed <= allowed
    return Check(
        name,
        passed,
        f"{elapsed} days after {start_label}; the agreement allows {allowed}.",
    )


def _from_checks(clause: Clause, checks: list[Check]) -> PolicyResult:
    outcome = "valid" if checks and all(check.passed for check in checks) else "invalid"
    return PolicyResult(
        outcome=outcome,
        checks=checks,
        citation_id=clause.clause_id,
        citation_heading=clause.heading,
        citation_text=clause.text,
        agreement_id=clause.agreement_id,
    )


def _invalid(clause: Clause, checks: list[Check]) -> PolicyResult:
    return _from_checks(clause, checks)


def _require(clauses: list[Clause], kind: str, case_id: str) -> Clause:
    found = _one(clauses, kind)
    if found is None:
        raise RuntimeError(f"{case_id} has no {kind} clause in its agreements")
    return found


def _one(clauses: list[Clause], kind: str) -> Clause | None:
    found = [clause for clause in clauses if clause.kind == kind]
    if len(found) > 1:
        raise RuntimeError(f"Expected one {kind} clause, found {len(found)}")
    return found[0] if found else None


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _same_money(left: float, right: float) -> bool:
    return round(left * 100) == round(right * 100)


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _qty(amount: float) -> str:
    if float(amount).is_integer():
        return str(int(amount))
    return f"{amount:g}"
