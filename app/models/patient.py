import enum
from datetime import date

from sqlalchemy import JSON, Date, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Sex(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class Patient(Base, UUIDMixin, TimestampMixin):
    """Synthetic patient record (Synthea). Never load real PHI into this table."""

    __tablename__ = "patients"

    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[Sex] = mapped_column(Enum(Sex, name="patient_sex"), nullable=False)

    # Week 3 feeds these into the rule engine.
    conditions: Mapped[list] = mapped_column(JSON, default=list)   # ICD-10 codes
    medications: Mapped[list] = mapped_column(JSON, default=list)
    lab_values: Mapped[dict] = mapped_column(JSON, default=dict)   # {"egfr": 42.0}
