# Django Web Shell

The first Q-Bet browser entry point is a small Django server-rendered shell. It deliberately contains no calculation logic and uses fixture-only execution engine statuses.

## Local Start

SQLite remains the default local operational database:

```powershell
python -m pip install -e .
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/` for the shell and `http://127.0.0.1:8000/health/` for the JSON health response. Local sign-in is available at `/accounts/login/`; it uses Django's built-in sessions, CSRF protection, and authentication.

Local development can opt into PostgreSQL with the same `QBET_DATABASE_URL` used by hosted deployments. This is the migration seam for eventually removing SQLite from operational development; SQLite remains the default until the custom Q-Bet repositories are migrated deliberately.

## Environment

- `QBET_DJANGO_SECRET_KEY`: required unique secret outside local development.
- `QBET_DJANGO_DEBUG`: `true` locally, `false` in deployed environments.
- `QBET_DJANGO_ALLOWED_HOSTS`: comma-separated host allowlist.
- `QBET_DATABASE_URL`: PostgreSQL connection URL. In hosted mode this enables Django's persistent auth/session database. A Supabase transaction-pooler URL is appropriate for Vercel's serverless runtime.
- `QBET_HOSTED_PREVIEW`: marks the restricted hosted deployment boundary.
- `QBET_SIMULATION_MODE_ENABLED`: remains `false` in hosted deployments until simulation persistence is cloud-ready.

`SUPABASE_QBET_TOKEN` is a Supabase Management API credential for automation and administration. It is not a PostgreSQL password and is not used by Django as `QBET_DATABASE_URL`.

## Supabase / PostgreSQL Bootstrap

The hosted Django shell uses ordinary Django migrations against PostgreSQL. Configure `QBET_DATABASE_URL` with the connection string from the Q-Bet Supabase project's **Connect** dialog, then run:

```powershell
python manage.py migrate --noinput
```

For Vercel, configure `QBET_DATABASE_URL` separately for Preview and Production as needed. The connection string must remain in environment/secrets configuration and must never be committed.

Once the PostgreSQL URL is configured and migrations have been applied, hosted registration, login, logout, dashboard/session preferences, engine-detail pages, and the basic account boundary can use persistent Django state. Routes that still depend on Q-Bet-specific SQLite repositories remain fail-closed with HTTP 503 instead of falling back to Vercel's ephemeral filesystem.

Django remains the authentication/session boundary in this milestone. Supabase Auth is not introduced here; a later integration may map a verified Supabase Auth identity to a Django user if the product needs it.

## Persistence Split

Q-Bet intentionally keeps transactional application state and analytical workloads separate:

- **Supabase/PostgreSQL**: hosted Django authentication, sessions, permissions, and future operational/ledger state.
- **SQLite**: transitional local operational persistence for repositories not migrated yet.
- **DuckDB**: local analytics, replay, backtesting, and historical analytical workloads only.

DuckDB must not be used for authentication, sessions, provider state, ledger authority, or writable Vercel persistence.
