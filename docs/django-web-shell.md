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
- `QBET_DJANGO_ALLOWED_HOSTS`: comma-separated exact host allowlist. Hosted runtimes reject `*` and suffix-wide entries such as `.vercel.app`; the current exact Vercel deployment host is also derived from `VERCEL_URL`.
- `QBET_DATABASE_URL`: required PostgreSQL connection URL for every normal Q-Bet runtime. A Supabase transaction-pooler URL is appropriate for Vercel's serverless runtime; a session/direct connection can be used for administrative migration work where appropriate.
- `QBET_SUPABASE_URL` and `QBET_SUPABASE_STORAGE_SECRET_KEY`: server-only configuration for private profile-avatar object storage. Keep the bucket private and configure the key only in the deployment environment's secret store; never expose it to templates, browser code, or logs. Avatar files are normalized before upload and served only through the authenticated Django account route.
- `QBET_HOSTED_RUNTIME`: explicit hosted-runtime signal used by Preview and Production security checks. Vercel `preview` and `production` environments are also detected automatically.
- `QBET_HOSTED_PREVIEW`: retained as a compatibility signal for Preview; it no longer defines the complete hosted security boundary.
- `QBET_SESSION_COOKIE_AGE_SECONDS`: optional positive session lifetime override. The default is 12 hours.
- `QBET_SIMULATION_MODE_ENABLED`: bootstrap/default Simulation availability. Preview CI sets this to `false`; another deployed environment may explicitly enable it.
- `QBET_TEST_DATABASE_URL`: disposable PostgreSQL connection used by pytest. Tests replace `QBET_DATABASE_URL` inside the pytest process with this value so they cannot accidentally mutate the shared Supabase database.
- `QBET_EMAIL_BACKEND`: optional explicit Django email backend. Local/test runtime defaults to the in-memory backend; hosted preview/runtime defaults to SMTP so verification/reset links are never printed to console logs.
- `QBET_DEFAULT_FROM_EMAIL`: sender address for verification and password-reset mail.
- `QBET_EMAIL_HOST`, `QBET_EMAIL_PORT`, `QBET_EMAIL_HOST_USER`, `QBET_EMAIL_HOST_PASSWORD`, `QBET_EMAIL_USE_TLS`: SMTP configuration for hosted account-security mail. Keep credentials in environment/secrets configuration only.

`SUPABASE_QBET_TOKEN` is a Supabase Management API credential for automation and administration. It is not a PostgreSQL password and is not used by Django as `QBET_DATABASE_URL`.

## Supabase / PostgreSQL Bootstrap

Q-Bet uses ordinary Django migrations against PostgreSQL. Configure `QBET_DATABASE_URL` with the connection string from the Q-Bet Supabase project's **Connect** dialog, then run:

```powershell
python manage.py migrate --noinput
```

For Vercel, configure `QBET_DATABASE_URL` for Preview and Production as required by the chosen environment model. The connection string must remain in environment/secrets configuration and must never be committed.

### Hosted Readiness And Migration Order

`/health/` is an operational readiness probe, not merely a Django-process liveness check. It performs a read-only PostgreSQL connection check and asks Django for the current migration plan. It returns HTTP 200 only when the configured authoritative database is reachable and has no unapplied migrations. Database failures, inconsistent migration state, or pending migrations return HTTP 503 with a stable non-sensitive reason code.

The Vercel preview smoke always inspects `/health/`. A deployment is operationally ready only when the endpoint reports `status=ok` and `persistence=ready`.

Preview and `develop` / staging validation treat the specific `migrations_pending` state as a visible warning rather than a merge-blocking code failure because normal CI is not allowed to mutate the shared hosted database. Other readiness failures such as database unavailability, invalid migration state, or an unexpected health response still fail validation. This keeps schema drift visible without creating a CI deadlock that could only be resolved by an unauthorized database write. Production promotion still requires operators to apply the intended migrations so `/health/` can return `persistence=ready`.

Deploying Q-Bet never runs migrations from a web request, serverless cold start, or normal preview smoke. Schema changes remain an explicit operator action:

```powershell
python manage.py migrate --check
python manage.py migrate --plan
python manage.py migrate --noinput
```

The first two commands are useful read-only checks. Run the final migration command only with the intended target `QBET_DATABASE_URL` and the required operational authorization. After applying migrations, `/health/` should return `status=ok` and `persistence=ready`.

Normal GitHub Actions still use disposable PostgreSQL. CI additionally runs `makemigrations --check --dry-run` and `migrate --check` so model changes without committed migrations and incomplete disposable test schemas fail before deployment.

PostgreSQL is the durable operational source of truth for Django authentication, sessions, simulation control/run state, provider state, simulation reports/log records, and the current monitoring/reporting inputs. Missing `QBET_DATABASE_URL` now fails configuration explicitly; Q-Bet does not silently create a local SQLite database or switch to an ephemeral Vercel file.

## Account Security

New public registrations remain inactive until the address is verified through a one-time, 24-hour verification link. Opening the link with GET is intentionally read-only and renders an explicit confirmation step; activation requires a CSRF-protected POST that revalidates the token and expiry under database row locks. Expired inactive registrations remain unusable and cleanup is performed only during explicit unsafe account actions, never as a side effect of GET. Password changes use Django's authenticated password-change boundary; forgotten-password flows use Django's one-time, 24-hour password-reset tokens.

Verification and reset token paths are redacted by Q-Bet request logging. The hosted email path uses SMTP rather than the console backend so token-bearing links are not written to application logs. If hosted mail delivery is unavailable, registration fails closed and does not leave a newly created inactive account behind.

Hosted Preview and Production both enforce the non-development Django secret, `DEBUG=false`, PostgreSQL SSL, Secure/HttpOnly/SameSite cookies, HTTPS redirect, explicit browser security headers, and exact Host validation. Production additionally emits one-year HSTS without subdomain/preload claims because Q-Bet does not own the shared `vercel.app` parent domain. The current CSP is intentionally compatible with Django Admin and existing inline frontend fragments; stricter nonce/hash CSP is a later hardening seam. See [Phase 3 Web Security Review](security-review-phase3.md) for the reviewed route matrix, residual risks, and Phase 4 handoff.

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
