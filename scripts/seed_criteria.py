"""Seed hand-authored eligibility trees for three real trials.

These are *approximations*. ClinicalTrials.gov gives eligibility as free text;
turning it into a boolean tree is the parser's job (not built yet). Until then
these trees are written by hand from what trials of this kind typically require,
attached to real NCT IDs so the demo shows an actual study.

Deliberately not reverse-engineered from the seeded patients: the criteria are
written as a protocol would state them, and whatever verdicts the ten patients
produce is the honest output.

Run: docker compose exec api python -m scripts.seed_criteria
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.criteria import CriteriaNode, NodeType, Operator
from app.models.trial import Trial


def leaf(field, op, value, *, raw, exclusion=False, order=0) -> CriteriaNode:
    return CriteriaNode(
        node_type=NodeType.LEAF,
        field=field,
        operator=op,
        value=value,
        raw_text=raw,
        is_exclusion=exclusion,
        ordering=order,
    )


def branch(kind, children, *, raw=None, exclusion=False, order=0) -> CriteriaNode:
    node = CriteriaNode(
        node_type=kind, raw_text=raw, is_exclusion=exclusion, ordering=order
    )
    node.children = children
    return node


def bypass_t2dm() -> CriteriaNode:
    """NCT07278115 -- gastric bypass for T2DM remission."""
    return branch(
        NodeType.AND,
        [
            leaf("age", Operator.GTE, "18", raw="Age 18 years or older", order=0),
            leaf("age", Operator.LTE, "65", raw="Age 65 years or younger", order=1),
            leaf("conditions", Operator.CONTAINS, "E11",
                 raw="Diagnosis of type 2 diabetes mellitus", order=2),
            leaf("lab_values.egfr", Operator.GTE, "60",
                 raw="Adequate renal function (eGFR >= 60 mL/min/1.73m2)", order=3),
            # Exclusion: a leaf flagged is_exclusion inverts, so a patient WITH
            # a malignancy fails here.
            leaf("conditions", Operator.CONTAINS, "C50", exclusion=True,
                 raw="Exclusion: active malignancy", order=4),
        ],
        raw="Inclusion and exclusion criteria (hand-authored approximation)",
    )


def elderly_diabetes() -> CriteriaNode:
    """NCT06842459 -- functional decline in elderly diabetics."""
    return branch(
        NodeType.AND,
        [
            leaf("age", Operator.GTE, "65", raw="Age 65 years or older", order=0),
            leaf("conditions", Operator.CONTAINS, "E11",
                 raw="Diagnosis of type 2 diabetes mellitus", order=1),
            leaf("lab_values.hba1c", Operator.GTE, "7.0",
                 raw="HbA1c >= 7.0% at screening", order=2),
        ],
        raw="Inclusion criteria (hand-authored approximation)",
    )


def asthma_aerosol() -> CriteriaNode:
    """NCT07326995 -- inhalation aerosol for asthma.

    The OR branch is the interesting part: either a documented asthma diagnosis
    or current rescue-inhaler use qualifies, which is how protocols usually
    phrase it.
    """
    return branch(
        NodeType.AND,
        [
            leaf("age", Operator.GTE, "12", raw="Age 12 years or older", order=0),
            branch(
                NodeType.OR,
                [
                    leaf("conditions", Operator.CONTAINS, "J45",
                         raw="Documented asthma diagnosis", order=0),
                    leaf("medications", Operator.CONTAINS, "albuterol",
                         raw="Current short-acting beta-agonist use", order=1),
                ],
                raw="Asthma established by diagnosis or rescue inhaler use",
                order=1,
            ),
            leaf("conditions", Operator.CONTAINS, "J44", exclusion=True,
                 raw="Exclusion: concomitant COPD", order=2),
        ],
        raw="Inclusion and exclusion criteria (hand-authored approximation)",
    )


TREES = {
    "NCT07278115": bypass_t2dm,
    "NCT06842459": elderly_diabetes,
    "NCT07326995": asthma_aerosol,
}


def stamp_trial_id(node: CriteriaNode, trial_id) -> None:
    """Set trial_id on every node in the subtree.

    The children relationship propagates parent_id on flush, but trial_id is a
    separate non-null FK that SQLAlchemy has no reason to infer -- children
    would insert with a null trial_id. Denormalising it onto every node is
    deliberate: it makes "all nodes for this trial" a single indexed query
    rather than a recursive CTE.
    """
    node.trial_id = trial_id
    for child in node.children:
        stamp_trial_id(child, trial_id)


async def seed() -> None:
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        for nct_id, build in TREES.items():
            trial = await session.scalar(select(Trial).where(Trial.nct_id == nct_id))
            if trial is None:
                print(f"{nct_id}: not in the database, skipping")
                continue

            # Idempotent: drop any existing tree for this trial first. The
            # self-referencing FK cascades, so deleting roots takes children.
            await session.execute(
                delete(CriteriaNode).where(CriteriaNode.trial_id == trial.id)
            )

            root = build()
            stamp_trial_id(root, trial.id)
            session.add(root)
            print(f"{nct_id}: seeded tree")

        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
