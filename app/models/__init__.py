"""Model package.

These imports exist for their side effect: each module registers its class
with the declarative registry. Trial and CriteriaNode reference each other
through relationship() strings and import each other only under
TYPE_CHECKING (to avoid a circular import), so at runtime neither name
resolves unless something has already loaded both modules. Importing them
here means importing any part of app.models registers all of them.

Without this the API 500s on the first ORM query with
"expression 'CriteriaNode' failed to locate a name".
"""

from app.models.criteria import CriteriaNode  # noqa: F401
from app.models.patient import Patient  # noqa: F401
from app.models.trial import Trial  # noqa: F401
from app.models.user import User  # noqa: F401

__all__ = ["CriteriaNode", "Patient", "Trial", "User"]
