"""SQLite case file. Operating metrics and the manager's notes live here; drafts do too."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .errors import AlreadyPosted, RatingNotConfirmed
from .models import (
    CashApplication,
    Destination,
    Dispute,
    OneOnOne,
    PeopleCase,
    Promise,
    QAReview,
    Rating,
    Teammate,
    Transmission,
)
from .seed import ACTIVITY, ROSTER

SCHEMA = """
CREATE TABLE IF NOT EXISTS teammates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    tenure_months INTEGER NOT NULL,
    scenario TEXT NOT NULL,
    scenario_label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_applications (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    applied_on TEXT NOT NULL,
    amount INTEGER NOT NULL,
    reference TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    customer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    due_on TEXT NOT NULL,
    kept INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    customer TEXT NOT NULL,
    opened_on TEXT NOT NULL,
    closed_on TEXT
);
CREATE TABLE IF NOT EXISTS qa_reviews (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    reviewed_on TEXT NOT NULL,
    score REAL NOT NULL,
    comment TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS one_on_ones (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    met_on TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overtime_weeks (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    week_ending TEXT NOT NULL,
    hours REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS closed_work (
    id INTEGER PRIMARY KEY,
    teammate_id TEXT NOT NULL,
    closed_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    teammate_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset(self) -> None:
        with self._connect() as conn:
            for table in (
                "cases",
                "closed_work",
                "overtime_weeks",
                "one_on_ones",
                "qa_reviews",
                "disputes",
                "promises",
                "cash_applications",
                "teammates",
            ):
                conn.execute(f"DELETE FROM {table}")
            for person in ROSTER:
                conn.execute(
                    """
                    INSERT INTO teammates (id, name, role, tenure_months, scenario, scenario_label)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        person.id,
                        person.name,
                        person.role,
                        person.tenure_months,
                        person.scenario,
                        person.scenario_label,
                    ),
                )
                bundle = ACTIVITY[person.id]
                conn.executemany(
                    """
                    INSERT INTO cash_applications (teammate_id, applied_on, amount, reference)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (person.id, item.applied_on.isoformat(), item.amount, item.reference)
                        for item in bundle["cash"]
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO promises (teammate_id, customer, amount, due_on, kept)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (person.id, item.customer, item.amount, item.due_on.isoformat(), int(item.kept))
                        for item in bundle["promises"]
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO disputes (teammate_id, customer, opened_on, closed_on)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (person.id, item.customer, item.opened_on.isoformat(), _iso(item.closed_on))
                        for item in bundle["disputes"]
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO qa_reviews (teammate_id, reviewed_on, score, comment)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (person.id, item.reviewed_on.isoformat(), item.score, item.comment)
                        for item in bundle["qa"]
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO one_on_ones (teammate_id, met_on, note)
                    VALUES (?, ?, ?)
                    """,
                    [(person.id, item.met_on.isoformat(), item.note) for item in bundle["notes"]],
                )
                conn.executemany(
                    """
                    INSERT INTO overtime_weeks (teammate_id, week_ending, hours)
                    VALUES (?, ?, ?)
                    """,
                    [(person.id, day.isoformat(), hours) for day, hours in bundle["overtime"]],
                )
                # One row per closed case so workload is a count, not a stored total.
                conn.executemany(
                    "INSERT INTO closed_work (teammate_id, closed_on) VALUES (?, ?)",
                    [(person.id, "2026-09-30") for _ in range(bundle["cases_closed"])],
                )

    def ensure_seeded(self) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM teammates").fetchone()["n"]
        if count == 0:
            self.reset()

    def teammate_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM teammates ORDER BY rowid").fetchall()
        return [row["id"] for row in rows]

    def get_teammate(self, teammate_id: str) -> Teammate:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM teammates WHERE id = ?", (teammate_id,)).fetchone()
        if row is None:
            raise KeyError(teammate_id)
        return Teammate(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            tenure_months=row["tenure_months"],
            scenario=row["scenario"],
            scenario_label=row["scenario_label"],
        )

    def list_teammates(self) -> list[Teammate]:
        return [self.get_teammate(teammate_id) for teammate_id in self.teammate_ids()]

    def cash_applications(self, teammate_id: str) -> list[CashApplication]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cash_applications WHERE teammate_id = ? ORDER BY applied_on",
                (teammate_id,),
            ).fetchall()
        return [
            CashApplication(
                amount=row["amount"],
                applied_on=date.fromisoformat(row["applied_on"]),
                reference=row["reference"],
            )
            for row in rows
        ]

    def promises(self, teammate_id: str) -> list[Promise]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM promises WHERE teammate_id = ? ORDER BY due_on",
                (teammate_id,),
            ).fetchall()
        return [
            Promise(
                amount=row["amount"],
                kept=bool(row["kept"]),
                customer=row["customer"],
                due_on=date.fromisoformat(row["due_on"]),
            )
            for row in rows
        ]

    def disputes(self, teammate_id: str) -> list[Dispute]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM disputes WHERE teammate_id = ? ORDER BY opened_on",
                (teammate_id,),
            ).fetchall()
        return [
            Dispute(
                customer=row["customer"],
                opened_on=date.fromisoformat(row["opened_on"]),
                closed_on=_parse(row["closed_on"]),
            )
            for row in rows
        ]

    def qa_reviews(self, teammate_id: str) -> list[QAReview]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_reviews WHERE teammate_id = ? ORDER BY reviewed_on",
                (teammate_id,),
            ).fetchall()
        return [
            QAReview(
                score=row["score"],
                comment=row["comment"],
                reviewed_on=date.fromisoformat(row["reviewed_on"]),
            )
            for row in rows
        ]

    def one_on_ones(self, teammate_id: str) -> list[OneOnOne]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM one_on_ones WHERE teammate_id = ? ORDER BY met_on",
                (teammate_id,),
            ).fetchall()
        return [OneOnOne(met_on=date.fromisoformat(row["met_on"]), note=row["note"]) for row in rows]

    def overtime_hours(self, teammate_id: str) -> list[float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT hours FROM overtime_weeks WHERE teammate_id = ? ORDER BY week_ending",
                (teammate_id,),
            ).fetchall()
        return [float(row["hours"]) for row in rows]

    def cases_closed(self, teammate_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM closed_work WHERE teammate_id = ?",
                (teammate_id,),
            ).fetchone()
        return int(row["n"])

    def note_texts(self, teammate_id: str) -> set[str]:
        texts = {item.note for item in self.one_on_ones(teammate_id)}
        texts.update(item.comment for item in self.qa_reviews(teammate_id))
        return texts

    def save_case(self, case: PeopleCase) -> PeopleCase:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cases (teammate_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(teammate_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (case.teammate_id, case.model_dump_json(), now),
            )
        return case

    def get_case(self, teammate_id: str) -> PeopleCase | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM cases WHERE teammate_id = ?", (teammate_id,)).fetchone()
        if row is None:
            return None
        return PeopleCase.model_validate_json(row["payload"])

    def require_case(self, teammate_id: str) -> PeopleCase:
        case = self.get_case(teammate_id)
        if case is None:
            raise KeyError(teammate_id)
        return case

    def any_cases(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
        return int(row["n"]) > 0

    def confirm(self, teammate_id: str, rating: Rating, manager: str) -> PeopleCase:
        manager_name = manager.strip()
        if rating not in ("exceeds", "meets", "below"):
            raise ValueError("Rating must be exceeds, meets, or below.")
        if not manager_name:
            raise ValueError("A manager name is required to confirm a rating.")
        case = self.require_case(teammate_id)
        case.confirmed_rating = rating
        case.confirmed_by = manager_name
        case.confirmed_at = datetime.now(timezone.utc).isoformat()
        return self.save_case(case)

    def post(self, teammate_id: str, destination: Destination) -> PeopleCase:
        if destination not in ("hr", "employee"):
            raise ValueError("Destination must be hr or employee.")
        case = self.require_case(teammate_id)
        if case.confirmed_rating is None:
            raise RatingNotConfirmed(teammate_id)
        if any(item.destination == destination for item in case.transmissions):
            raise AlreadyPosted(teammate_id, destination)
        case.transmissions.append(
            Transmission(
                destination=destination,
                rating=case.confirmed_rating,
                sent_at=datetime.now(timezone.utc).isoformat(),
                body=_packet(case, destination),
            )
        )
        return self.save_case(case)


def _packet(case: PeopleCase, destination: Destination) -> str:
    """What actually leaves the cockpit. The stay conversation is not in it."""
    assert case.confirmed_rating is not None
    who = "HR" if destination == "hr" else case.name
    lines = [
        f"To: {who}",
        f"Confirmed rating: {case.confirmed_rating}.",
        f"Confirmed by: {case.confirmed_by}.",
    ]
    if case.confirmed_rating != case.performance.rating:
        lines.append(
            f"The drafted narrative was written at the system rating of {case.performance.rating} "
            "and was not rewritten."
        )
    lines.append("Sent only after the manager confirmed the rating.")
    lines.append("")
    lines.append(case.performance.narrative)
    return "\n".join(lines)
