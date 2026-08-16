from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.trial import TrialStatus


class TrialRead(BaseModel):
    """Read model for a trial.

    from_attributes lets Pydantic build this straight off the SQLAlchemy ORM
    object, which replaces the hand-written dict comprehension in the endpoint.
    Beyond being less code, it means the OpenAPI schema and the wire format can
    never drift apart -- with manual serialisation, adding a column to the model
    silently leaves /docs describing a response the API no longer returns.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nct_id: str
    title: str
    brief_summary: str | None = None
    # Serialises to the enum's value ("recruiting"), matching the old dict's
    # t.status.value -- so this is not a breaking change for existing clients.
    status: TrialStatus
    phase: str | None = None
    conditions: list = []
    start_date: date | None = None
    created_at: datetime
