"""Four AR teammates with different quarter outcomes. Notes are the manager's own."""

from __future__ import annotations

from datetime import date, timedelta

from .models import CashApplication, Dispute, OneOnOne, Promise, QAReview, Teammate

CUSTOMERS = (
    "Northline Hardware",
    "Brightwater Clinics",
    "Harbor & Co.",
    "Fieldstone Mills",
    "Kepler Freight",
    "Sable Office Supply",
    "Redbird Foods",
    "Lumen Dental",
)


def _dates(start: date, count: int, step: int = 7) -> list[date]:
    return [start + timedelta(days=step * index) for index in range(count)]


def _cash(amounts: list[int], start: date) -> list[CashApplication]:
    days = _dates(start, len(amounts), 14)
    return [
        CashApplication(amount=amount, applied_on=day, reference=f"REM-{day:%m%d}-{index + 1}")
        for index, (amount, day) in enumerate(zip(amounts, days))
    ]


def _promises(kept: int, broken: int, start: date) -> list[Promise]:
    rows: list[Promise] = []
    for index in range(kept + broken):
        rows.append(
            Promise(
                amount=4_500 + (index % 5) * 250,
                kept=index < kept,
                customer=CUSTOMERS[index % len(CUSTOMERS)],
                due_on=start + timedelta(days=3 * index),
            )
        )
    return rows


def _disputes(cycle_days: list[int], start: date) -> list[Dispute]:
    rows: list[Dispute] = []
    for index, days in enumerate(cycle_days):
        opened = start + timedelta(days=9 * index)
        rows.append(
            Dispute(
                customer=CUSTOMERS[index % len(CUSTOMERS)],
                opened_on=opened,
                closed_on=opened + timedelta(days=days),
            )
        )
    return rows


def _qa(scores: list[float], comments: list[str], start: date) -> list[QAReview]:
    days = _dates(start, len(scores), 12)
    return [
        QAReview(score=score, comment=comment, reviewed_on=day)
        for score, comment, day in zip(scores, comments, days)
    ]


def _weeks(hours: list[float], start: date = date(2026, 8, 7)) -> list[tuple[date, float]]:
    return [(start + timedelta(days=7 * index), value) for index, value in enumerate(hours)]


MAYA = Teammate(
    id="TL-MAYA",
    name="Maya Chen",
    role="Senior cash applications specialist",
    tenure_months=46,
    scenario="stable_high_performer",
    scenario_label="Stable high performer",
)
ANDRE = Teammate(
    id="TL-ANDRE",
    name="Andre Walsh",
    role="Collector",
    tenure_months=38,
    scenario="burnout_flight_risk",
    scenario_label="Solid collector burning out",
)
PRIYA = Teammate(
    id="TL-PRIYA",
    name="Priya Nair",
    role="Cash applications associate",
    tenure_months=3,
    scenario="ramping_new_hire",
    scenario_label="New hire still ramping",
)
JORDAN = Teammate(
    id="TL-JORDAN",
    name="Jordan Hale",
    role="Collector",
    tenure_months=18,
    scenario="below_standard",
    scenario_label="Below standard",
)

ROSTER: tuple[Teammate, ...] = (MAYA, ANDRE, PRIYA, JORDAN)


def roster_ids() -> list[str]:
    return [person.id for person in ROSTER]


# Raw activity. Tests lock the totals these rows add up to.
ACTIVITY: dict[str, dict] = {
    MAYA.id: {
        "cash": _cash([800_000, 720_000, 530_000, 400_000], date(2026, 7, 6)),
        "promises": _promises(46, 2, date(2026, 7, 2)),
        "disputes": _disputes([5, 6, 6, 7, 7], date(2026, 7, 3)),
        "qa": _qa(
            [98, 97, 96, 99, 97],
            [
                "Calls are clear and complete. Customers leave the line knowing the next step.",
                "Remittance coding matched the backup on every item in the sample.",
                "She explained an unapplied difference without leaving the customer guessing.",
                "Documentation on the unapplied cash was complete.",
                "The customer confirmed the application before the call ended.",
            ],
            date(2026, 7, 9),
        ),
        "notes": [
            OneOnOne(
                met_on=date(2026, 7, 22),
                note=(
                    "Maya said she is proud of the unapplied-cash cleanup and wants to mentor "
                    "Priya on deduction codes. She called this the best queue she has had."
                ),
            ),
            OneOnOne(
                met_on=date(2026, 9, 3),
                note=(
                    "She walked me through three payments she cleared without help and said she "
                    "wants the same standard used across the queue."
                ),
            ),
        ],
        "overtime": _weeks([2, 1, 3, 2, 2, 1, 3, 2]),
        "cases_closed": 148,
    },
    ANDRE.id: {
        "cash": _cash([640_000, 580_000, 420_000, 280_000], date(2026, 7, 6)),
        "promises": _promises(34, 6, date(2026, 7, 2)),
        "disputes": _disputes([10, 11, 12, 12, 12], date(2026, 7, 3)),
        "qa": _qa(
            [90, 92, 91, 90, 92],
            [
                "Tone on the last three calls was short. The customer got the right information, "
                "but Andre did not leave room for questions.",
                "The promise date was recorded correctly.",
                "Dispute notes were complete.",
                "Cash application on the sample was accurate.",
                "He answered with the right invoice number.",
            ],
            date(2026, 7, 9),
        ),
        "notes": [
            OneOnOne(
                met_on=date(2026, 8, 14),
                note=(
                    "Andre said the hours are catching up with him and he is not sure he can keep "
                    "this pace through year-end. He was quiet in the team huddle and said he has "
                    "stopped taking the overflow queue."
                ),
            ),
            OneOnOne(
                met_on=date(2026, 9, 18),
                note=(
                    "Andre told me he has been interviewing for another role and that he is "
                    "withdrawing from the extra projects. He still closes his own queue, but he "
                    "said the job does not feel sustainable."
                ),
            ),
        ],
        "overtime": _weeks([12, 14, 11, 13, 12, 15, 11, 13]),
        "cases_closed": 186,
    },
    PRIYA.id: {
        "cash": _cash([300_000, 280_000, 340_000, 300_000], date(2026, 7, 6)),
        "promises": _promises(18, 4, date(2026, 7, 2)),
        "disputes": _disputes([12, 13, 14, 14, 14], date(2026, 7, 3)),
        "qa": _qa(
            [84, 86, 85, 88, 87],
            [
                "Accurate coding on standard remittances. Still needs a prompt on deduction codes. "
                "Improving week over week.",
                "A deduction code was left blank until she asked for the list.",
                "Standard remittances were applied to the right invoices.",
                "She corrected a batch before it posted.",
                "The customer name on the remittance matched the account.",
            ],
            date(2026, 7, 9),
        ),
        "notes": [
            OneOnOne(
                met_on=date(2026, 9, 11),
                note=(
                    "Priya likes seeing a cash batch go to zero and brings questions to the 1:1 "
                    "instead of sitting on deduction codes she does not know yet."
                ),
            ),
        ],
        "overtime": _weeks([4, 5, 3, 4, 6, 4, 5, 4]),
        "cases_closed": 78,
    },
    JORDAN.id: {
        "cash": _cash([300_000, 280_000, 350_000, 350_000], date(2026, 7, 6)),
        "promises": _promises(22, 14, date(2026, 7, 2)),
        "disputes": _disputes([18, 20, 21, 22, 24], date(2026, 7, 3)),
        "qa": _qa(
            [76, 79, 80, 77, 78],
            [
                "Follow-up dates are missed. Two disputes sat with no customer contact for more than a week.",
                "A promised call-back was not on the account when QA checked.",
                "The dispute reason was recorded, but the next step was blank.",
                "Two invoices in the sample had no collector note after the customer wrote in.",
                "The balance quoted on the call did not match the open items.",
            ],
            date(2026, 7, 9),
        ),
        "notes": [
            OneOnOne(
                met_on=date(2026, 9, 4),
                note=(
                    "Jordan pushed back when I walked through the broken promises. He said the "
                    "disputes are messy and that he wants another quarter to turn the numbers around."
                ),
            ),
        ],
        "overtime": _weeks([3, 2, 4, 2, 3, 1, 4, 2]),
        "cases_closed": 95,
    },
}
