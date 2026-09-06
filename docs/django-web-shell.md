# Django Web Shell

The Q-Bet browser entry point is a small Django server-rendered shell. It deliberately contains no calculation logic; calculations, risk, liquidity, persistence, and execution remain behind their typed boundaries.

## Local Start

Local Q-Bet no longer has an operational SQLite fallback. The locally started web application uses PostgreSQL/Supabase through the same `QBET_DATABASE_URL` seam as the hosted application.

```powershell
python -m pip install -e .
$env:QBET_DATABASE_URL='postgresql://...'
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/` for the shell and `http://127.0.0.1:8000/health/` for the JSON health response. Local sign-in is available at `/accounts/login/`; it uses Django's built-in sessions, CSRF protection, and authentication persisted in PostgreSQL.

With the intended Q-Bet development configuration, local and hosted application instances point to the same Supabase/PostgreSQL database. Durable changes made through one instance are therefore visible to the other without database synchronization or a separate local source of truth.

## Environment

- `QBET_DJANGO_SECRET_KEY`: required unique secret outside local development.
- `QBET_DJANGO_DEBUG`: `true` locally, `false` in deployed environments.
- `QBET_DJANGO_ALLOWED_HOSTS`: comma-separated host allowlist.
- `QBET_DATABASE_URL`: required PostgreSQL connection URL for every normal Q-Bet runtime. A Supabase transaction-pooler URL is appropriate for Vercel's serverless runtime; a session/direct connection can be used for administrative migration work where appropriate.
- `QBET_HOSTED_PREVIEW`: marks a hosted Vercel-style runtime so hosted security requirements such as SSL and disabled debug are enforced.
- `QBET_SIMULATION_MODE_ENABLED`: bootstrap/default Simulation availability. Preview CI sets this to `false`; another deployed environment may explicitly enable it.
- `QBET_TEST_DATABASE_URL`: disposable PostgreSQL connection used by pytest. Tests replace `QBET_DATABASE_URL` inside the pytest process with this value so they cannot accidentally mutate the shared Supabase database.

`SUPABASE_QBET_TOKEN` is a Supabase Management API credential for automation and administration. It is not a PostgreSQL password and is not used by Django as `QBET_DATABASE_URL`.

## Supabase / PostgreSQL Bootstrap

Q-Bet uses ordinary Django migrations against PostgreSQL. Configure `QBET_DATABASE_URL` with the connection string from the Q-Bet Supabase project's **Connect** dialog, then run:

```powershell
python manage.py migrate --noinput
```

For Vercel, configure `QBET_DATABASE_URL` for Preview and Production as required by the chosen environment model. The connection string must remain in environment/secrets configuration and must never be committed.

PostgreSQL is the durable operational source of truth for Django authentication, sessions, simulation control/run state, provider state, simulation reports/log records, and the current monitoring/reporting inputs. Missing `QBET_DATABASE_URL` now fails configuration explicitly; Q-Bet does not silently create a local SQLite database or switch to an ephemeral Vercel file.

## Django Admin

Django Auth remains authoritative. Supabase Auth is not introduced by this persistence migration.

Bootstrap the first cloud superuser while `QBET_DATABASE_URL` points at Supabase/PostgreSQL:

```powershell
python manage.py createsuperuser
```

Then use `/admin/` as the central browser-based user and permission administration surface. Authorized admins can create, deactivate, and delete users; grant/remove Staff and Superuser rights; and manage Django groups and permissions.

Q-Bet protects the final active staff superuser at two layers:

- Django validation/signals/admin reject deletion, deactivation, or loss of `is_staff` / `is_superuser` when no other active staff superuser exists.
- PostgreSQL has a trigger guard so queryset/bulk-style mutations cannot bypass the rule.

After a second active staff superuser exists, another superuser may be demoted, deactivated, or deleted normally.

## Legacy SQLite Import

SQLite is retained only as a read-only legacy input. It is not part of normal runtime persistence.

Existing simulation history and provider state can be copied once into PostgreSQL with:

```powershell
python manage.py import_legacy_sqlite --simulation-db path\to\qbet-simulation.sqlite3 --provider-db path\to\provider-state.sqlite3
```

The importer preserves existing identifiers/payloads where possible, reports imported/skipped/conflicting entries, and does not silently overwrite conflicting PostgreSQL state. It may be re-run safely for already identical data. Disposable/test Django users do not have to be imported.

## Persistence Split

Q-Bet keeps durable operational state and analytical workloads separate:

- **Supabase/PostgreSQL**: authoritative shared operational storage for users, auth, sessions, permissions, provider state, workflow/control state, simulation reports/log records, monitoring inputs, and future ledger state.
- **DuckDB**: runtime-local analytics, replay, backtesting, simulation/fast-processing, and historical analytical workloads. Local and Vercel runtimes have separate DuckDB contexts; they are not synchronized.
- **SQLite**: legacy import source only.

DuckDB is not an authentication, session, provider-state, report-authority, or ledger database. A writable DuckDB file inside a Vercel runtime must not be treated as durable cloud storage. Results that must survive redeployment or be visible across runtimes are persisted back to PostgreSQL.

## Test Isolation

Normal tests must not point at shared Supabase. Start or provide a disposable PostgreSQL database and set:

```powershell
$env:QBET_TEST_DATABASE_URL='postgresql://qbet:qbet@127.0.0.1:5432/qbet_test'
python -m pytest
```

GitHub Actions supplies its own PostgreSQL 16 service and sets both the test URL and Django operational URL for the test job. This keeps production/shared cloud state out of CI while testing the same PostgreSQL semantics used at runtime.
