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
