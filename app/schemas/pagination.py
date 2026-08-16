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


class OffsetPage(BaseModel, Generic[ItemT]):
    """Offset-based page, used only by ranked search.

    Search cannot use the keyset cursor: keyset needs a sort key that is
    stable, unique, and independent of the query. ts_rank is a float that
    ties constantly and is recomputed per query string, so a cursor encoding
    it would break the moment the search terms changed. Relevance ordering
    and stable cursors are genuinely in tension; search takes relevance,
    /trials keeps keyset for browsing.

    Same reasoning as Page on `total`: no exact count, because counting
    means a full scan on every request. has_more comes from fetching
    limit + 1 rows and discarding the extra.
    """

    items: list[ItemT]
    offset: int = 0
    has_more: bool = False
