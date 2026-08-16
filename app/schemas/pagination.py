from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """Envelope for cursor-paginated collections.

    Deliberately no `total` field: an exact count requires a full scan on every
    request, which throws away the reason we moved off OFFSET. Clients paginate
    on `next_cursor is not None`, not on arithmetic over a total.
    """

    items: list[ItemT]
    next_cursor: str | None = None
    has_more: bool = False
