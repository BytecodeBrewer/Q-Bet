# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

Q-Bet already has a working deterministic sports core around `BonusEngine` and `SportsCapitalEngine`. The calculation layer uses `Decimal` and covers qualifying bets, free bets, two-way arbitrage, multi-outcome dutching, dynamic rounding, fees, commission, tax handling, stake and liquidity limits, and liability. Normalized market-data contracts plus the `Sports Match Builder` validate provider-neutral snapshots before engine evaluation. Real market and result collectors are not connected yet; the current collector implementation is deterministic and in-memory behind typed `DataCollector` and `ApiAdapter` contracts.

The connected sports path already runs through `WorkflowOrchestrator`, correlation IDs and structured transitions, `OperationalRiskLayer`, targeted `RequestHandler` refresh hooks, and the proposal-only `LiquidityChecker`. From there, the implemented end-to-end target is Simulation: `WorkflowSimulationRunner` evaluates the two sports engines with virtual capital, progress and run state, safe failure handling, and persisted reports. Supabase/PostgreSQL is now the durable operational source of truth for Django auth/sessions, simulation control/run state, simulation reports/log records, and provider state. DuckDB remains separate for runtime-local analytics, ordered replay, and backtesting workloads.

The Django web application provides registration and authentication, a user-facing Execution dashboard, and administrator-only Simulation and Monitoring surfaces. Staff can enable and start deterministic simulations, inspect progress and reports, export CSV/JSON, and use presentation settings plus session-persisted dashboard ordering. Django Admin is the central user/permission administration surface for users, staff/superuser access, groups, and permissions. A server-side and PostgreSQL guard prevents deleting, deactivating, or demoting the last active staff superuser. Execution and Simulation stay separated; the Execution dashboard currently reports live state as unavailable because no real execution source is connected.

The operational and tooling layer is also in place. GitHub Actions runs Ruff, Pyright, pytest, Django checks, and package builds before creating Vercel Preview/Staging deployments. CI uses an isolated PostgreSQL service rather than the shared Supabase database. Both a locally started Q-Bet web application and the Vercel-hosted application use `QBET_DATABASE_URL`; normal runtime no longer falls back to SQLite. SQLite remains only as a read-only legacy import source. Preview deployments keep Simulation disabled through environment configuration, while a deployed environment may enable it explicitly once its PostgreSQL migrations are current.

**Still missing:**

- conformant production market/result API adapters, concrete smart polling, and live provider refresh clients;
- the `PortfolioLedger` capital authority with reservations, settlement, realized P/L, cost tracking, and capital-movement proposals;
- bank and provider adapters beyond research/mock boundaries, including sandbox balance, transaction, and funding flows;
- controlled live execution with separate live queues/history, pre-execution revalidation, approvals, provider order submission, and settlement;
- a clean customer-facing Reporting model separated from internal Monitoring/diagnostics, plus stronger multi-user ownership and isolation;
- production implementations for `TicketEngine`, `PredictionMarketEngine`, `CryptoYieldEngine`, and `MLEdgeLayer`.

## Local Web Setup

Normal local Q-Bet runtime uses PostgreSQL/Supabase as the same operational source of truth as the deployed application. Configure `QBET_DATABASE_URL` before starting Django; there is no SQLite runtime fallback.

```powershell
$env:QBET_DATABASE_URL='postgresql://...'
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

For everyday local development against the shared Q-Bet cloud state, use the appropriate Supabase PostgreSQL connection string. Do not commit the connection string or database password.

The first cloud superuser can be created with `python manage.py createsuperuser` while `QBET_DATABASE_URL` points to Supabase. Further user, staff, superuser, group, and permission administration is available through `/admin/`. Q-Bet protects the last active staff superuser from deletion, deactivation, or demotion.

### Legacy SQLite Import

SQLite is no longer an active runtime database. Existing provider state and simulation history can be imported once into PostgreSQL with the explicit legacy command:

```powershell
python manage.py import_legacy_sqlite --simulation-db path\to\qbet-simulation.sqlite3 --provider-db path\to\provider-state.sqlite3
```

The importer opens SQLite read-only, reports imported/skipped/conflicting rows, and is designed to be safely re-run without silently overwriting authoritative PostgreSQL conflicts. Disposable/test Django users do not need to be migrated.

## Validation

Automated tests must never use the shared production Supabase database. Point `QBET_TEST_DATABASE_URL` to an isolated/disposable PostgreSQL database before running pytest locally:

```powershell
$env:QBET_TEST_DATABASE_URL='postgresql://qbet:qbet@127.0.0.1:5432/qbet_test'
python -m pip install . -r requirements-dev.txt
python -m ruff check .
python -m pyright
python -m pytest
```

For explicit Django migration/configuration checks, point `QBET_DATABASE_URL` at the intended PostgreSQL target:

```powershell
$env:QBET_DATABASE_URL=$env:QBET_TEST_DATABASE_URL
python manage.py migrate --noinput
python manage.py check
python -m build
```

GitHub Actions provides an isolated PostgreSQL 16 service automatically for its test job.

## Vercel Preview / Staging CD

GitHub Actions creates a Vercel Preview deployment only after the Ruff, Pyright, Django/test, and package-build gates succeed. Pull requests from this repository and successful pushes to `develop` use the dedicated Vercel project named `q-bet`; the workflow never deploys with `--prod`.

Configure these GitHub repository values for the deployment workflow:

- secret `VERCEL_TOKEN`: a Vercel token with access to the Q-Bet team
- variable `VERCEL_ORG_ID`: the Vercel team/org identifier
- variable `VERCEL_PROJECT_ID`: the existing Q-Bet Vercel project identifier
- secret `QBET_DJANGO_SECRET_KEY`: a non-development Django secret used by hosted deployments

The existing `q-bet` Vercel project is targeted explicitly by its project/account identifiers. The existing portfolio Vercel project is not used or modified.

Vercel must provide `QBET_DATABASE_URL` through project environment configuration. Preview and Production can use the same Supabase/PostgreSQL source of truth when that is the intended environment model. Missing `QBET_DATABASE_URL` is now a startup configuration error rather than a request-time fallback to read-only/SQLite behavior.

The CI preview explicitly supplies `QBET_HOSTED_PREVIEW=true`, `QBET_DJANGO_DEBUG=false`, the Vercel host allowlist, and `QBET_SIMULATION_MODE_ENABLED=false`. Production may enable Simulation deliberately through `QBET_SIMULATION_MODE_ENABLED=true`; durable simulation reports and control state are stored in PostgreSQL rather than Vercel-local files.

The deployment job verifies the live preview with Vercel's authenticated curl command, including `/health/`, the public shell, `/accounts/login/`, and `/static/qbet_web/app.css`.

## Persistence Architecture

Q-Bet deliberately separates durable operational state from runtime-local analytical workloads:

- **Supabase/PostgreSQL** is the durable shared source of truth for authentication, sessions, permissions, provider state, simulation/control state, reports, monitoring inputs, workflow state, and future ledger state. Local and hosted web instances reference this same database in normal use.
- **DuckDB** is the per-runtime analytics/replay/backtesting/fast-processing layer. A local Q-Bet process and a Vercel runtime have separate DuckDB contexts. DuckDB state is not synchronized and must not be treated as durable shared storage.
- **SQLite** is legacy-only. It may be read by the one-time importer but is not part of normal application persistence.

Any analytical/simulation result that must survive process termination or be visible across local/hosted instances must be written back through the PostgreSQL operational boundary.

`SUPABASE_QBET_TOKEN` is a Supabase Management API credential for automation/administration. It is not a PostgreSQL connection string. The Django application requires `QBET_DATABASE_URL` from the Supabase project's Connect dialog, stored only in environment/secrets configuration. See [Django Web Shell](docs/django-web-shell.md) for bootstrap details.

## Version 1 Target

Version 1 should include:

- a complete matched-betting engine from data intake to strategy result, execution plan, and report
- workflow orchestration plus liquidity checking that can route capital and stay ready for `TicketEngine`, `PredictionMarketEngine`, and `CryptoYieldEngine`
- simulation mode for every engine, including placeholder/sandbox engines where not fully implemented
- controlled real execution for supported matched-betting workflows
- Playwright-based web collectors where APIs are unavailable or insufficient
- API adapter structure for crypto delta-neutral and prediction-market engines
- bank connectivity for balances and approved funding flows
- tests for calculations, strategy logic, mock integrations, and safety-critical workflows
- a compact web UI with a customer-facing Execution dashboard, administrator-only Simulation and Monitoring controls, engine views, and performance reports

The current UI already supports theme/font preferences and session-scoped dashboard widget ordering. Future visual polish should stay secondary to workflow safety, clear data boundaries, and useful operational behavior.

## Engine Portfolio

Q-Bet is organized around real engine names instead of broad tier nicknames:

- `BonusEngine`: promotional matched betting, qualifying bets, free bets, reloads, cashback, and promo conversion.
- `SportsCapitalEngine`: sports arbitrage, odds boosts, real-capital matched betting, and dutching.
- `TicketEngine`: event-driven secondary ticket arbitrage.
- `PredictionMarketEngine`: prediction-market making, order-book arbitrage, and supported API strategies.
- `CryptoYieldEngine`: crypto delta-neutral and funding-rate strategies through API adapters.
- `MLEdgeLayer`: later statistical support for value detection, fair-odds modeling, and drift checks.

## Core Architecture

```text
Data Aggregation
    -> Engine-specific Preparation
    -> Calculation
    -> Domain Risk where needed
    -> Liquidity Check
    -> Simulation / Execution
```

See [Pipeline Architecture](docs/pipeline-architecture.md) for the Mermaid model and component naming.

Initial matched-betting strategy focus:

- qualifying bets
- free bet strategies
- arbitrage detection
- dutching
- stake optimization and dynamic rounding
- account-operation risk warnings

## Guardrails

Q-Bet should support research, simulation, reporting, lawful integrations, and user-approved execution. It should not move money from a bank account without explicit approval. It should not run as unmanaged full autonomy; unattended runs are capped at 48 hours.

## Project Documents

- [Expectation Model](docs/expectation-model.md)
- [Pipeline Architecture](docs/pipeline-architecture.md)
- [Django Web Shell](docs/django-web-shell.md)
