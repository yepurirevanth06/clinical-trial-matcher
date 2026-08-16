"""ClinicalTrials.gov API v2 client.

Deliberately thin: fetch pages, normalise into dicts our model understands,
and leave persistence to the caller. Keeping I/O separate from the write path
means the sync task can be tested with a fake client and no network.
"""

import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# Ask for only the fields we store. The full study record is enormous and
# most of it is never read -- requesting less is the cheapest optimisation
# available on any third-party API.
FIELDS = ",".join([
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.descriptionModule.briefSummary",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.startDateStruct",
    "protocolSection.designModule.phases",
    "protocolSection.conditionsModule.conditions",
    "protocolSection.contactsLocationsModule.locations",
    "protocolSection.eligibilityModule.eligibilityCriteria",
])

STATUS_MAP = {
    "RECRUITING": "recruiting",
    "ACTIVE_NOT_RECRUITING": "active_not_recruiting",
    "COMPLETED": "completed",
    "TERMINATED": "terminated",
    "WITHDRAWN": "terminated",
    "SUSPENDED": "terminated",
}


def _dig(d: dict, *path: str, default: Any = None) -> Any:
    """Walk a nested dict without a pyramid of .get() calls."""
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def normalise(study: dict) -> dict | None:
    """One raw study -> the fields our Trial model stores.

    Returns None for studies we can't use (no NCT id, or a status outside our
    enum). Skipping is correct here: a partial record would be worse than an
    absent one, and the sync reports how many it dropped.
    """
    proto = study.get("protocolSection", {})
    nct_id = _dig(proto, "identificationModule", "nctId")
    if not nct_id:
        return None

    raw_status = _dig(proto, "statusModule", "overallStatus", default="")
    status = STATUS_MAP.get(raw_status)
    if status is None:
        return None

    start_raw = _dig(proto, "statusModule", "startDateStruct", "date")
    start_date = None
    if start_raw:
        # The API returns "2024-03" for month precision and "2024-03-15" for
        # day precision. Normalise both to a real date.
        parts = start_raw.split("-")
        try:
            start_date = date(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 1,
                int(parts[2]) if len(parts) > 2 else 1,
            )
        except (ValueError, IndexError):
            start_date = None

    phases = _dig(proto, "designModule", "phases", default=[])
    locations = _dig(proto, "contactsLocationsModule", "locations", default=[])

    return {
        "nct_id": nct_id,
        "title": _dig(proto, "identificationModule", "briefTitle", default=""),
        "brief_summary": _dig(proto, "descriptionModule", "briefSummary"),
        "status": status,
        "start_date": start_date,
        "phase": ", ".join(phases) if phases else None,
        "conditions": _dig(proto, "conditionsModule", "conditions", default=[]),
        "locations": [
            {
                "facility": loc.get("facility"),
                "city": loc.get("city"),
                "state": loc.get("state"),
                "country": loc.get("country"),
            }
            for loc in locations[:20]  # cap: some trials list hundreds of sites
        ],
        "eligibility_text": _dig(proto, "eligibilityModule", "eligibilityCriteria"),
    }


class ClinicalTrialsClient:
    def __init__(self, timeout: float = 30.0, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max_retries

    async def iter_studies(
        self, condition: str | None = None, page_size: int = 100, max_pages: int = 50
    ) -> AsyncIterator[dict]:
        """Yield normalised studies, following nextPageToken.

        max_pages is a guard, not a preference: without it a bad filter can
        walk half a million records and the task never finishes.
        """
        params: dict[str, Any] = {
            "pageSize": page_size,
            "fields": FIELDS,
            "filter.overallStatus": "RECRUITING",
        }
        if condition:
            params["query.cond"] = condition

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            page_token: str | None = None
            for page in range(max_pages):
                if page_token:
                    params["pageToken"] = page_token

                data = await self._get_with_retry(http, params)
                if data is None:
                    logger.error("ctgov: giving up at page %s", page)
                    return

                for study in data.get("studies", []):
                    normalised = normalise(study)
                    if normalised is not None:
                        yield normalised

                page_token = data.get("nextPageToken")
                if not page_token:
                    return

    async def _get_with_retry(
        self, http: httpx.AsyncClient, params: dict
    ) -> dict | None:
        """Exponential backoff on transient failures. 4xx other than 429 is a
        bug in our request, so retrying just wastes time -- we fail fast."""
        import asyncio

        for attempt in range(self._max_retries):
            try:
                resp = await http.get(BASE_URL, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    logger.error("ctgov: non-retryable %s", exc.response.status_code)
                    return None
            except (httpx.TimeoutException, httpx.TransportError):
                pass

            if attempt < self._max_retries - 1:
                await asyncio.sleep(2**attempt)

        return None
