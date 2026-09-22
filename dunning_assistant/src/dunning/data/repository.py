"""Pandas-backed access to the seeded AR tables."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import Settings, get_settings
from ..domain import AccountSnapshot, Customer
from .seed import TABLES, build_dataset, write_dataset

DATE_COLUMNS = {
    "customers": ["relationship_start"],
    "invoices": ["issue_date", "due_date", "paid_date"],
    "payments": ["payment_date"],
    "promises": ["promised_on", "promised_date"],
    "emails": ["sent_at"],
}


def _coerce_dates(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    for column in DATE_COLUMNS.get(name, []):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.date
    return frame


def load_frames(settings: Settings | None = None, *, autoseed: bool = True) -> dict[str, pd.DataFrame]:
    """Load the CSV tables, generating them on first use."""
    settings = settings or get_settings()
    missing = [t for t in TABLES if not (settings.data_dir / f"{t}.csv").exists()]
    if missing:
        if not autoseed:
            raise FileNotFoundError(f"Missing seed tables: {', '.join(missing)}. Run `dunning seed`.")
        try:
            write_dataset(settings)
        except OSError:
            return {name: _coerce_dates(name, frame) for name, frame in build_dataset(settings).items()}

    frames = {}
    for table in TABLES:
        frame = pd.read_csv(settings.data_dir / f"{table}.csv")
        frames[table] = _coerce_dates(table, frame)
    if "disputed" in frames["invoices"].columns:
        frames["invoices"]["disputed"] = frames["invoices"]["disputed"].astype(bool)
    if "kept" in frames["promises"].columns:
        frames["promises"]["kept"] = frames["promises"]["kept"].astype(bool)
    return frames


class AccountRepository:
    """Assembles a per-account snapshot that the agent graph reads from."""

    def __init__(self, settings: Settings | None = None, frames: dict[str, pd.DataFrame] | None = None):
        self.settings = settings or get_settings()
        self.frames = frames or load_frames(self.settings)

    @property
    def as_of(self) -> date:
        return self.settings.as_of

    def account_ids(self) -> list[str]:
        return self.frames["customers"]["account_id"].tolist()

    def customers(self) -> pd.DataFrame:
        return self.frames["customers"]

    def customer(self, account_id: str) -> Customer:
        rows = self.frames["customers"]
        match = rows[rows["account_id"] == account_id]
        if match.empty:
            raise KeyError(f"Unknown account_id {account_id!r}. Known: {', '.join(self.account_ids())}")
        return Customer(**match.iloc[0].to_dict())

    def _for_account(self, table: str, account_id: str, sort_by: str) -> pd.DataFrame:
        frame = self.frames[table]
        subset = frame[frame["account_id"] == account_id].copy()
        if sort_by in subset.columns:
            subset = subset.sort_values(sort_by).reset_index(drop=True)
        return subset

    def snapshot(self, account_id: str) -> AccountSnapshot:
        customer = self.customer(account_id)
        return AccountSnapshot(
            customer=customer,
            invoices=self._for_account("invoices", account_id, "due_date"),
            payments=self._for_account("payments", account_id, "payment_date"),
            promises=self._for_account("promises", account_id, "promised_date"),
            emails=self._for_account("emails", account_id, "sent_at"),
            as_of=self.as_of,
        )

    def portfolio_summary(self) -> pd.DataFrame:
        """One row per account: open balance, oldest days past due, archetype label."""
        invoices = self.frames["invoices"]
        open_invoices = invoices[invoices["status"] != "paid"].copy()
        open_invoices["outstanding"] = open_invoices["amount"] - open_invoices["amount_paid"]
        open_invoices["days_past_due"] = open_invoices["due_date"].map(lambda d: (self.as_of - d).days)
        grouped = (
            open_invoices.groupby("account_id")
            .agg(
                open_invoices=("invoice_id", "count"),
                past_due_balance=("outstanding", "sum"),
                oldest_days_past_due=("days_past_due", "max"),
            )
            .reset_index()
        )
        return self.frames["customers"][["account_id", "name", "segment", "archetype"]].merge(
            grouped, on="account_id", how="left"
        )


def get_repository(settings: Settings | None = None) -> AccountRepository:
    settings = settings or get_settings()
    return AccountRepository(settings)
