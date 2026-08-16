# Week 1 — your task list

Work top to bottom. One branch and one PR per item.

## Day 1 — get it running
- [ ] `git init`, first commit, push to GitHub, **protect `main`** (Settings →
      Branches → require PR + require CI to pass)
- [ ] `cp .env.example .env`, generate a real `SECRET_KEY`
- [ ] `docker compose up` — confirm http://localhost:8000/health returns ok
- [ ] Generate and apply the first Alembic migration
- [ ] `pytest -v` — 11 tests should pass

## Day 2 — understand what you have
Do not skip this. You need to be able to defend every line in an interview.
- [ ] Read `app/core/security.py`. Why does `decode_token` check the `type` claim?
- [ ] Read `app/core/deps.py`. Why is `require_role` a *factory* that returns a
      dependency, rather than a dependency itself?
- [ ] Read `app/errors.py`. Trace what happens to a Pydantic validation failure.
- [ ] Read `app/models/criteria.py`. Draw the tree for: "age ≥ 18 AND (diabetes
      OR hypertension) AND NOT pregnant" on paper.

## Day 3–4 — build these yourself
- [ ] `POST /v1/patients` + `GET /v1/patients/{id}` with a `PatientCreate` schema
- [ ] Load 100 Synthea patients via a seed script (`scripts/seed.py`)
- [ ] Admin-only `PATCH /v1/users/{id}/role`, with a test that a coordinator gets 403
- [ ] Add `updated_at` assertions to a model test

## Day 5 — pagination, properly
- [ ] Replace the stub in `list_trials` with real keyset pagination:
      encode `(created_at, id)` as a base64 cursor, decode on the way in
- [ ] Write a test that inserts a row *between* two page fetches and asserts no
      item is skipped or duplicated. This test is the whole point.

## Day 6–7 — harden and document
- [ ] Get `mypy app` clean with no `--ignore-missing-imports`
- [ ] Add a coverage gate: `pytest --cov=app --cov-fail-under=80`
- [ ] Write the "Architecture notes" you'd defend in an interview
- [ ] Open week 2 as GitHub Issues before you start it

## Interview questions this week's code should let you answer
1. Why access + refresh tokens instead of one long-lived token?
2. What attack does the identical-login-error behaviour prevent?
3. Why is offset pagination wrong for a feed that's being written to?
4. What breaks if you `git pull` a teammate's migration and you both created one?
5. Why `expire_on_commit=False` on the session maker?

Answer all five out loud before you start week 2.

## Week 2 perf numbers (measured 2026-08-16)

For the README performance section. All on the trials table, docker compose.

| Query | Rows in table | Plan | Time |
|---|---|---|---|
| `ILIKE '%insulin%'` on title + brief_summary | 1000 | Seq Scan | 9.513 ms |
| `search_vector @@ plainto_tsquery('insulin')` | 1000 | Bitmap Index Scan | 0.363 ms |
| `'type 2 diabetes'` + ts_rank_cd order, limit 21 | 2103 | Seq Scan (planner's choice) | 19.0 ms median |
| same, `enable_seqscan = off` | 2103 | Bitmap Index Scan | 14.6 ms median |
| same, `random_page_cost = 1.1` | 2103 | Bitmap Index Scan (planner switched) | 14.5 ms |

Medians are from 3 runs each; individual samples 19.0/19.6/17.6 and 13.6/14.9/14.6.

### The finding
At 22% selectivity (470/2103) the planner chose a seq scan that was ~23%
slower than the index path. Cause was cost model, not the index: it priced
the bitmap path at 1265 vs 431 for seq scan, driven by the GIN scan's 1016
startup cost. Default `random_page_cost = 4.0` assumes rotational media;
at 1.1 (SSD-realistic) the bitmap path priced at 429 and the planner picked
it unprompted.

Caveat for the write-up: do NOT present 1.1 as "the fix". It is correct for
SSD and wrong for spinning disks or network-attached volumes. Framing is
"identified the parameter, measured its effect, would set it per deployment".

Also worth noting: row estimate was 108 vs 470 actual even after ANALYZE.
Postgres has no good selectivity stats for tsvector matches.

### Benchmarking gotcha hit along the way
`LIMIT` without `ORDER BY` lets the planner stop early, so it measures time
to first N rows, not query completion. An early `insulin` measurement looked
fast for exactly this reason and was thrown out.

## Open items
1. README performance section (numbers above, highest value)
2. Cache tests -- zero coverage; hit/miss JSON equivalence + version-bump invalidation
3. conftest uses Base.metadata.create_all, not `alembic upgrade head`. Root cause
   of the search_vector drift bug. The jsonb_path_ops index has the same exposure.
