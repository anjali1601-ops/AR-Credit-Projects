"""Errors the API and CLI turn into responses."""


class SurveillanceError(Exception):
    """Base error for the surveillance service."""


class AccountNotFound(SurveillanceError):
    """No portfolio account matches the id."""


class ReviewNotFound(SurveillanceError):
    """No periodic review matches the id."""


class ReviewTransitionError(SurveillanceError):
    """The review cannot move to the requested status."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class NarratorError(SurveillanceError):
    """The memo narrator could not produce a narrative."""
