# Baton API — local development

## Prerequisites
- Python 3.11+
- Docker (for Postgres 16)

## First run

```bash
cd api

# 1. Python environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 2. Environment
copy .env.example .env          # Windows (cp on macOS/Linux) — defaults work for local dev

# 3. Database (Postgres 16 on host port 5433)
docker compose up -d db

# 4. Migrations
alembic upgrade head

# 5. Run the API
uvicorn app.main:app --reload
```

API: http://localhost:8000 · interactive docs: http://localhost:8000/docs

## Emails in dev
With `EMAIL_CONN` empty, invite/reset emails are printed to the console instead of sent.

## Tests

```bash
docker compose up -d db   # tests need the dev Postgres running
pytest
```

Tests run against a separate `baton_test` database (created automatically) so they never
touch dev data.

## Migrations
Schema changes ship as Alembic migrations — never hand-edit the DB.

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```
