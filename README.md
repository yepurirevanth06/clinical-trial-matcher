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

## Performance

Three measured comparisons from week 2. Numbers were taken on a single laptop
under Docker Compose (Postgres 16, Redis 7), so they are illustrative rather
than benchmark-grade — the methodology notes below say which figures are solid
and which are not.

### 1. Substring matching to full-text search

The naive query is `ILIKE '%term%'` over `title` and `brief_summary`. Replaced
with a `GENERATED ALWAYS AS ... STORED` tsvector column and a GIN index.

| Query | Plan | Time |
|---|---|---|
| `ILIKE '%insulin%'` | Seq Scan | 9.51 ms |
| `search_vector @@ plainto_tsquery('insulin')` | Bitmap Index Scan | 0.36 ms |

Measured with `EXPLAIN ANALYZE` on 1,000 rows.

The speedup is the smaller half of the win. `ILIKE` does substring matching, so
it misses "insulins" contextually while matching "insulinoma" spuriously.
`to_tsvector` stems, so the search is over lexemes rather than character runs —
a correctness improvement, not just a faster one.

`setweight` tags title (A), summary (B), and conditions (C) so `ts_rank_cd` can
score a title hit above a passing mention. Without the weights every match ties
and results come back effectively unordered.

### 2. The planner declining the index

At 2,103 rows, `'type 2 diabetes'` matches 470 rows — 22% selectivity. The
planner chose a sequential scan and was **wrong**:

| Configuration | Plan | Median time |
|---|---|---|
| Default | Seq Scan | 19.0 ms |
| `enable_seqscan = off` | Bitmap Index Scan | 14.6 ms |
| `random_page_cost = 1.1` | Bitmap Index Scan (chosen) | 14.5 ms |

Medians of three runs each: 19.0/19.6/17.6 and 13.6/14.9/14.6.

The cause was the cost model, not the index. Postgres priced the bitmap path at
1265 against 431 for the seq scan, driven almost entirely by the GIN scan's
startup cost of 1016. The default `random_page_cost = 4.0` assumes rotational
media; at 1.1 the bitmap path priced at 429 and the planner picked it without
being forced.

Two caveats worth stating. `random_page_cost = 1.1` is correct for SSD-backed
storage and wrong for spinning disks or network volumes with real seek latency —
the right value is a property of the deployment, not a tuning trick. And the row
estimate was 108 against 470 actual even immediately after `ANALYZE`, because
Postgres has no useful selectivity statistics for tsvector matches.

### 3. Redis cache on the search path

`GET /v1/trials/search` caches the serialised page under a key containing a
version counter, which the sync task increments — one operation invalidates
everything, versus `KEYS` + `DEL`, which is O(keyspace) and blocks Redis's
single thread.

| Request | Median | Samples |
|---|---|---|
| Warm (Redis hit) | 2.5 ms | 2.32 / 2.51 / 2.61 |
| Cold (query + serialisation) | 13 ms | 6.1 / 6.6 / 13.1 / 21.4 / 48.3 |

**These are end-to-end `curl` timings, not query execution**, so they are not
comparable to the `EXPLAIN ANALYZE` figures above.

The warm figure is solid: three samples inside a 13% spread, which is what you
would expect from a short deterministic path — one Redis read plus revalidation
of cached JSON. The cold figure is not. Five samples spanning 6–48 ms means the
variance exceeds the effect being claimed, so it belongs in the README as a
range rather than a point estimate. A cold request runs a query whose plan may
vary, touches heap pages that may or may not be cached, and validates ~200 rows
through Pydantic; none of that is stable at millisecond resolution on a laptop.

Getting a defensible cold number would need a quiet machine, a warmed page
cache, and enough samples to report a distribution. That work has not been done,
so the claim here is only that a cache hit is reliably faster — not by how much.
