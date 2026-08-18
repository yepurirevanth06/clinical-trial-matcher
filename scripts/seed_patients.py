"""Seed synthetic patients for local development.

Hand-authored rather than Synthea-generated: the point is to exercise the
evaluator's edge cases deliberately. Several patients are missing labs on
purpose so the three-valued logic has something to return UNKNOWN for, and
two sit on an age boundary.

Run: docker compose exec api python -m scripts.seed_patients
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.patient import Patient, Sex

# ICD-10: E11.9 type 2 diabetes, I10 hypertension, J45.909 asthma,
# N18.3 CKD stage 3, C50.919 breast cancer, J44.9 COPD.
PATIENTS = [
    # --- straightforward passes -------------------------------------------
    dict(external_id="SYN-0001", birth_date=date(1975, 3, 14), sex=Sex.FEMALE,
         conditions=["E11.9", "I10"], medications=["metformin", "lisinopril"],
         lab_values={"egfr": 88.0, "hba1c": 7.4}),
    dict(external_id="SYN-0002", birth_date=date(1962, 11, 2), sex=Sex.MALE,
         conditions=["E11.9"], medications=["insulin glargine"],
         lab_values={"egfr": 72.0, "hba1c": 8.9}),

    # --- missing labs: should evaluate UNKNOWN, not FAIL -------------------
    dict(external_id="SYN-0003", birth_date=date(1988, 6, 30), sex=Sex.FEMALE,
         conditions=["J45.909"], medications=["albuterol"],
         lab_values={}),
    dict(external_id="SYN-0004", birth_date=date(1955, 1, 20), sex=Sex.MALE,
         conditions=["N18.3", "I10"], medications=["amlodipine"],
         lab_values={"hba1c": 5.8}),  # no egfr despite CKD

    # --- age boundaries ----------------------------------------------------
    dict(external_id="SYN-0005", birth_date=date(2008, 8, 1), sex=Sex.OTHER,
         conditions=["J45.909"], medications=[],
         lab_values={"egfr": 110.0}),
    dict(external_id="SYN-0006", birth_date=date(2008, 12, 1), sex=Sex.FEMALE,
         conditions=[], medications=[],
         lab_values={"egfr": 105.0}),

    # --- exclusion fodder --------------------------------------------------
    dict(external_id="SYN-0007", birth_date=date(1970, 4, 5), sex=Sex.FEMALE,
         conditions=["C50.919", "E11.9"], medications=["tamoxifen", "metformin"],
         lab_values={"egfr": 65.0, "hba1c": 7.1}),
    dict(external_id="SYN-0008", birth_date=date(1949, 9, 12), sex=Sex.MALE,
         conditions=["J44.9", "I10"], medications=["tiotropium"],
         lab_values={"egfr": 48.0}),

    # --- renal impairment, common exclusion --------------------------------
    dict(external_id="SYN-0009", birth_date=date(1966, 2, 28), sex=Sex.MALE,
         conditions=["N18.3", "E11.9"], medications=["insulin lispro"],
         lab_values={"egfr": 29.0, "hba1c": 9.6}),
    dict(external_id="SYN-0010", birth_date=date(1993, 7, 19), sex=Sex.FEMALE,
         conditions=[], medications=[],
         lab_values={"egfr": 120.0, "hba1c": 5.1}),
]


async def seed() -> None:
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    created = skipped = 0
    async with maker() as session:
        for row in PATIENTS:
            # Idempotent: external_id is unique, so re-running is safe.
            existing = await session.scalar(
                select(Patient).where(Patient.external_id == row["external_id"])
            )
            if existing:
                skipped += 1
                continue
            session.add(Patient(**row))
            created += 1
        await session.commit()

    await engine.dispose()
    print(f"seeded {created} patient(s), skipped {skipped} existing")


if __name__ == "__main__":
    asyncio.run(seed())
