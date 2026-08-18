"""Match result schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.models.criteria import NodeType
from app.services.eligibility import Verdict


class NodeExplanation(BaseModel):
    """One node of the evaluated tree.

    Recursive: `children` is the same model, which Pydantic resolves because
    `from __future__ import annotations` defers the annotation and the
    model_rebuild() below closes the loop.

    from_attributes so this builds straight off the NodeResult dataclass the
    rule engine returns -- no hand-written conversion to drift out of sync.
    """

    model_config = ConfigDict(from_attributes=True)

    verdict: Verdict
    reason: str
    node_type: NodeType
    # The trial's own wording, kept so an explanation can quote the protocol
    # rather than a reconstruction of it.
    raw_text: str | None = None
    is_exclusion: bool = False
    children: list[NodeExplanation] = []


NodeExplanation.model_rebuild()


class MatchResult(BaseModel):
    """A patient evaluated against one trial's criteria."""

    patient_id: uuid.UUID
    external_id: str
    nct_id: str
    trial_title: str
    # Top-level verdict. UNKNOWN is a real outcome, not an error: it means at
    # least one criterion could not be decided from the data on file.
    verdict: Verdict
    explanation: NodeExplanation
