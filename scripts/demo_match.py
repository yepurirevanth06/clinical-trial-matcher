"""Print a match explanation for every seeded patient against one trial.

Throwaway demo, not a deliverable -- the real interface is the endpoint that
does not exist yet. This just proves the pieces connect.
"""

import asyncio
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.criteria import CriteriaNode
from app.models.patient import Patient
from app.models.trial import Trial
from app.services.eligibility import evaluate

AS_OF = date(2026, 8, 18)


def show(result, depth=0):
    pad = "  " * depth
    label = result.raw_text or f"[{result.node_type.value}]"
    print(f"{pad}{result.verdict.value.upper():8} {label}")
    if result.verdict.value != "pass" and not result.children:
        print(f"{pad}         -> {result.reason}")
    for child in result.children:
        show(child, depth + 1)


async def main(nct_id: str) -> None:
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        trial = await session.scalar(select(Trial).where(Trial.nct_id == nct_id))
        root = await session.scalar(
            select(CriteriaNode)
            .where(CriteriaNode.trial_id == trial.id, CriteriaNode.parent_id.is_(None))
            .options(selectinload(CriteriaNode.children).selectinload(CriteriaNode.children))
        )
        patients = list(await session.scalars(select(Patient).order_by(Patient.external_id)))

        print(f"\n{nct_id}  {trial.title[:70]}\n")
        for p in patients:
            result = evaluate(root, p, AS_OF)
            print(f"--- {p.external_id}  ({p.sex.value}, born {p.birth_date})")
            show(result)
            print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "NCT07278115"))
