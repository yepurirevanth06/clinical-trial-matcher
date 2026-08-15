import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.trial import Trial


class NodeType(str, enum.Enum):
    AND = "and"
    OR = "or"
    NOT = "not"
    LEAF = "leaf"


class Operator(str, enum.Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"


class CriteriaNode(Base, UUIDMixin, TimestampMixin):
    """One node of a trial's eligibility tree.

    Leaves carry a comparison (field/operator/value); AND, OR, and NOT nodes
    carry children. The week-3 rule engine walks this tree and returns a
    per-node explanation of why a patient passed or failed.
    """

    __tablename__ = "criteria_nodes"

    trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trials.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("criteria_nodes.id", ondelete="CASCADE"), index=True
    )
    node_type: Mapped[NodeType] = mapped_column(Enum(NodeType, name="node_type"), nullable=False)
    is_exclusion: Mapped[bool] = mapped_column(default=False, nullable=False)
    ordering: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Leaf-only fields.
    field: Mapped[str | None] = mapped_column(String(64))
    operator: Mapped[Operator | None] = mapped_column(Enum(Operator, name="criteria_operator"))
    value: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(String(1000))

    trial: Mapped["Trial"] = relationship(back_populates="criteria_nodes")
    children: Mapped[list["CriteriaNode"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["CriteriaNode | None"] = relationship(
        back_populates="children", remote_side="CriteriaNode.id"
    )
