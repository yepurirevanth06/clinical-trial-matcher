"""Single import point for Alembic autogenerate.

Every model module must be imported here or its table will be silently
missing from generated migrations.
"""

from app.db.base import Base  # noqa: F401
from app.models.criteria import CriteriaNode  # noqa: F401
from app.models.patient import Patient  # noqa: F401
from app.models.trial import Trial  # noqa: F401
from app.models.user import User  # noqa: F401
