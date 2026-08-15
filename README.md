# Clinical Trial Matching Platform

Matches synthetic patient records against clinical trial eligibility criteria and
explains **why** each patient passed or failed every clause.

> Uses synthetic patient data (Synthea). **Not for clinical use.**

## Stack

FastAPI (async) · PostgreSQL 16 · SQLAlchemy 2.0 · Alembic · Redis · Celery ·
React + TypeScript · Docker · GitHub Actions

## Status

| Week | Scope | State |
|---|---|---|
| 1 | Auth, RBAC, models, migrations, patients endpoints, CI | ✅ |
| 2 | ClinicalTrials.gov sync, search, caching, indexing | ⬜ |
| 3 | Explainable eligibility rule engine | ⬜ |
| 4 | React/TS frontend, E2E tests, observability, deploy | ⬜ |

## Quick start

```bash
cp .env.example .env
# generate a real secret:
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY

docker compose up -d db
docker compose up api

# in another shell — create the first migration and apply it
docker compose exec api alembic revision --autogenerate -m "initial schema"
docker compose exec api alembic upgrade head
```

API docs: http://localhost:8000/docs

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check app tests
mypy app --ignore-missing-imports
```

## Architecture notes

**Cursor pagination over offset.** Offset pagination skips or repeats rows when
records are inserted while a client is scrolling. Keyset pagination on
`(created_at, id)` is stable under concurrent writes.

**Typed JWTs.** Access and refresh tokens carry a `type` claim that is checked on
decode, so a refresh token can't be replayed as an access token. There is a test
for exactly this.

**Uniform login errors.** Unknown email and wrong password return an identical
401 body, so the endpoint can't be used to enumerate registered accounts.

**Role ranking.** Roles are ordered (`viewer < coordinator < admin`) so every
permission check is one integer comparison instead of a set membership test.

**Criteria as a tree.** Eligibility is a nested AND/OR/NOT tree of leaf
comparisons rather than a flat list, because real criteria nest. The week-3
engine walks it and returns a per-node result, which is what makes the output
explainable ("excluded: eGFR 42, requires ≥60").

## Benchmarks

_Week 2 fills this in — query times before and after indexing, measured with
`EXPLAIN ANALYZE`._

| Query | Before index | After index |
|---|---|---|
| _tbd_ | | |
