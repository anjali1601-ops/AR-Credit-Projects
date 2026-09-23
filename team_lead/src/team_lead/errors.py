"""Errors the confirm gate is allowed to raise."""


class RatingNotConfirmed(Exception):
    """A review, coaching note, or stay conversation was about to leave the cockpit."""

    def __init__(self, teammate_id: str):
        self.teammate_id = teammate_id
        super().__init__(
            f"The rating for {teammate_id} is not confirmed. "
            "Nothing was sent to HR or the employee."
        )


class AlreadyPosted(Exception):
    def __init__(self, teammate_id: str, destination: str):
        self.teammate_id = teammate_id
        self.destination = destination
        super().__init__(f"{teammate_id} was already sent to {destination}.")
