"""Loading seeded applicants from disk."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .models import CreditApplication


class ApplicantNotFound(KeyError):
    def __init__(self, applicant_id: str, available: list[str]) -> None:
        super().__init__(
            f"no applicant {applicant_id!r}; available: {', '.join(available) or 'none'}"
        )
        self.applicant_id = applicant_id
        self.available = available


def load_application(path: Path) -> CreditApplication:
    return CreditApplication.model_validate(json.loads(path.read_text()))


def list_applications(settings: Settings) -> list[CreditApplication]:
    directory = settings.applicants_dir
    if not directory.exists():
        return []
    return [load_application(p) for p in sorted(directory.glob("*.json"))]


def get_application(applicant_id: str, settings: Settings) -> CreditApplication:
    applications = list_applications(settings)
    for application in applications:
        if application.applicant_id == applicant_id:
            return application
    raise ApplicantNotFound(applicant_id, [a.applicant_id for a in applications])
