"""Pydantic schemas for patients."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.patient import Sex


def _years_since(birth: date, today: date | None = None) -> int:
    """Whole years elapsed since `birth`.

    Not `(today - birth).days // 365` — that assumes every year is 365 days
    and drifts by a day or more once leap years accumulate, which flips the
    answer for anyone whose birthday is near today's date.

    Instead: take the year difference, then subtract one if this year's
    birthday hasn't happened yet. Python compares tuples element-wise, so
    (month, day) < (month, day) is exactly the "has their birthday passed"
    question.
    """
    today = today or date.today()
    had_birthday = (today.month, today.day) >= (birth.month, birth.day)
    return today.year - birth.year - (0 if had_birthday else 1)


class PatientCreate(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=64)
    birth_date: date
    sex: Sex

    # default_factory, not default=[]. A plain default=[] would be evaluated
    # once at class-definition time and shared by every instance that omits
    # the field — one patient appending to `conditions` would mutate every
    # other patient's list. default_factory builds a fresh list per instance.
    conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    lab_values: dict[str, float] = Field(default_factory=dict)

    @field_validator("birth_date")
    @classmethod
    def birth_date_not_future(cls, v: date) -> date:
        # Raising ValueError inside a validator is how you signal invalid
        # input to Pydantic. FastAPI catches the resulting
        # RequestValidationError, and the handler in app/errors.py turns it
        # into the 422 envelope the rest of the API uses.
        if v > date.today():
            raise ValueError("birth_date cannot be in the future")
        return v


class PatientOut(BaseModel):
    # from_attributes lets Pydantic read a SQLAlchemy ORM object directly
    # (patient.external_id) instead of requiring a dict. Without it, returning
    # the model instance from the endpoint raises a validation error.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    birth_date: date
    sex: Sex
    conditions: list[str]
    medications: list[str]
    lab_values: dict[str, float]
    created_at: datetime

    # computed_field puts a derived value in the serialized response without
    # it existing as a column. Age is stored nowhere and can never go stale.
    # The type: ignore is a known Pydantic/mypy friction point with the
    # decorator ordering — @computed_field must wrap @property.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def age(self) -> int:
        return _years_since(self.birth_date)
