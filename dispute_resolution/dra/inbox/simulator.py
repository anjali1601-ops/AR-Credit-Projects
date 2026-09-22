"""Simulated mailbox.

No mail server: messages are rows in ``inbox_messages``. Anything that lands
with status ``unread`` is picked up by the reactive poller, whether it came from
the seed fixtures or was pushed in over the API.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from dra.db.models import InboxMessage
from dra.inbox.samples import SAMPLE_EMAILS


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def deliver(
    session: Session,
    *,
    sender: str,
    subject: str,
    body: str,
    sender_name: str = "",
    recipient: str = "ar@acme-supply.example",
    message_id: str | None = None,
    received_at: dt.datetime | None = None,
) -> InboxMessage:
    """Drop a new email into the simulated inbox."""
    message = InboxMessage(
        message_id=message_id or f"msg-{uuid.uuid4().hex[:12]}",
        sender=sender,
        sender_name=sender_name,
        recipient=recipient,
        subject=subject,
        body=body,
        received_at=received_at or _utcnow(),
        status="unread",
    )
    session.add(message)
    session.flush()
    return message


def load_samples(session: Session) -> list[InboxMessage]:
    """Idempotently load the demo emails."""
    now = _utcnow()
    loaded: list[InboxMessage] = []
    for sample in SAMPLE_EMAILS:
        existing = session.scalar(
            select(InboxMessage).where(InboxMessage.message_id == sample["message_id"])
        )
        if existing is not None:
            loaded.append(existing)
            continue
        loaded.append(
            deliver(
                session,
                message_id=str(sample["message_id"]),
                sender=str(sample["sender"]),
                sender_name=str(sample["sender_name"]),
                subject=str(sample["subject"]),
                body=str(sample["body"]),
                received_at=now - dt.timedelta(hours=float(sample["hours_ago"])),
            )
        )
    return loaded
