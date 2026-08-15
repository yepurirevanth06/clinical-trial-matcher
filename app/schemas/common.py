from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Cursor pagination. Offset pagination drifts when rows are inserted
    mid-scroll; a cursor does not."""

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
