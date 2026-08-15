"""Opaque keyset (cursor) pagination helpers."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import Select, literal, tuple_
from sqlalchemy.orm import InstrumentedAttribute

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

T = TypeVar("T")


class InvalidCursorError(ValueError):
    """Client supplied a cursor we can't parse. Maps to HTTP 400."""


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position of the last row of the previous page.

    Both the sort column AND the primary key are stored. `created_at` alone does
    not define a total order, and in this schema that is not a theoretical worry:
    created_at defaults to server-side now(), which in Postgres is the
    TRANSACTION timestamp -- so every trial upserted by a single sync task shares
    one identical created_at value. Without the UUID tiebreaker, a 100-row sync
    batch is one undifferentiated blob and pages through it would skip and repeat
    rows freely. The tiebreaker is doing the real work here, not backstopping it.
    """

    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps(
            {"c": self.created_at.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        )
        # urlsafe alphabet, padding stripped: this value is echoed straight back
        # as a bare query-string param, and '+' '/' '=' would all need escaping.
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, raw: str) -> Cursor:
        try:
            padded = raw + "=" * (-len(raw) % 4)  # restore the padding we stripped
            data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
            created_at = datetime.fromisoformat(data["c"])
            row_id = uuid.UUID(data["i"])
        # binascii.Error and json.JSONDecodeError are both ValueError subclasses,
        # and uuid.UUID() raises ValueError on a malformed hex string, so this
        # triple covers every way a hand-edited cursor can fail.
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise InvalidCursorError("malformed pagination cursor") from exc

        if created_at.tzinfo is None:
            # Column is TIMESTAMPTZ; asyncpg raises on aware/naive mismatch.
            created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(created_at=created_at, id=row_id)


def apply_keyset(
    stmt: Select[tuple[T]],
    *,
    sort_col: InstrumentedAttribute[Any],
    tiebreak_col: InstrumentedAttribute[Any],
    cursor: Cursor | None,
    limit: int,
) -> Select[tuple[T]]:
    """Order newest-first, seek past `cursor`, and fetch one sentinel row.

    The predicate is a SQL *row-value* comparison -- `(created_at, id) < (?, ?)` --
    not the hand-expanded `created_at < ? OR (created_at = ? AND id < ?)`. Postgres
    compiles a row-value comparison directly into one index range scan on the
    composite btree; the OR form regularly degrades into a BitmapOr or a filter
    over a wider scan. Same result set, very different plan.

    Postgres compares `uuid` bytewise, so a random UUIDv4 gives a total order even
    though it carries no time information -- which is all a tiebreaker needs.
    """
    stmt = stmt.order_by(sort_col.desc(), tiebreak_col.desc())

    if cursor is not None:
        stmt = stmt.where(
            tuple_(sort_col, tiebreak_col)
            < tuple_(
                # Bind each param with the column's own SQLAlchemy type. asyncpg
                # will not infer types inside a row constructor, and an untyped
                # UUID param arrives as text -- Postgres then either errors or
                # casts the COLUMN to text, silently discarding the index scan.
                literal(cursor.created_at, sort_col.type),
                literal(cursor.id, tiebreak_col.type),
            )
        )

    # limit + 1 -- the extra sentinel row tells us whether a next page exists
    # without paying for a second COUNT(*). It's sliced off before serialising.
    return stmt.limit(limit + 1)
