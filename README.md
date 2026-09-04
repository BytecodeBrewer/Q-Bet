# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

Q-Bet already has a working deterministic sports core around `BonusEngine` and `SportsCapitalEngine`. The calculation layer uses `Decimal` and covers qualifying bets, free bets, two-way arbitrage, multi-outcome dutching, dynamic rounding, fees, commission, tax handling, stake and liquidity limits, and liability. Normalized market-data contracts plus the `Sports Match Builder` validate provider-neutral snapshots before engine evaluation. Real market and result collectors are not connected yet; the current collector implementation is deterministic and in-memory behind typed `DataCollector` and `ApiAdapter` contracts.

The connected sports path already runs through `WorkflowOrchestrator`, correlation IDs and structured transitions, `OperationalRiskLayer`, targeted `RequestHandler` refresh hooks, and the proposal-only `LiquidityChecker`. From there, the implemented end-to-end target is Simulation: `WorkflowSimulationRunner` evaluates the two sports engines with virtual capital, progress and run state, safe failure handling, and persisted reports. SQLite adapters persist provider state and simulation reports locally; Django persists authentication, sessions, and simulation control/run state in its configured database. DuckDB is kept separate for ordered replay and backtesting streams.

The Django web application provides registration and authentication, a user-facing Execution dashboard, and administrator-only Simulation and Monitoring surfaces. Staff can enable and start deterministic simulations, inspect progress and reports, export CSV/JSON, and use presentation settings plus session-persisted dashboard ordering. Execution and Simulation stay separated; the Execution dashboard currently reports live state as unavailable because no real execution source is connected.

The operational and tooling layer is also in place. GitHub Actions runs Ruff, Pyright, pytest, Django checks, and package builds before creating Vercel Preview/Staging deployments. Django can use PostgreSQL through `QBET_DATABASE_URL`, and a Supabase migration hardens Django-owned tables in the public schema. SQLite remains the local operational fallback. Hosted previews deliberately disable Simulation and live execution and fail closed for SQLite-backed internal features that do not yet have cloud adapters.

**Still missing:**

- conformant production market/result API adapters, concrete smart polling, and live provider refresh clients;
- the `PortfolioLedger` capital authority with reservations, settlement, realized P/L, cost tracking, and capital-movement proposals;
- bank and provider adapters beyond research/mock boundaries, including sandbox balance, transaction, and funding flows;
- controlled live execution with separate live queues/history, pre-execution revalidation, approvals, provider order submission, and settlement;
- PostgreSQL/Supabase adapters for Q-Bet-specific provider/report operational repositories so Simulation and Monitoring can run in hosted mode;
- a clean customer-facing Reporting model separated from internal Monitoring/diagnostics, plus stronger multi-user ownership and isolation;
- production implementations for `TicketEngine`, `PredictionMarketEngine`, `CryptoYieldEngine`, and `MLEdgeLayer`.

## Local Web Setup

For the local Django shell, initialize Django's built-in authentication and session tables once before signing in:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

SQLite remains the default local operational database. Setting `QBET_DATABASE_URL` opts the Django web shell into PostgreSQL locally as well, providing the migration seam for eventually removing SQLite from operational development without using DuckDB as an application database.

## Validation

The same gates used by GitHub Actions can be run locally with:

```powershell
python -m pip install . -r requirements-dev.txt
python -m ruff check .
python -m pyright
python manage.py migrate --noinput
python -m pytest
python manage.py check
python -m build
```

## Vercel Preview / Staging CD

GitHub Actions creates a Vercel Preview deployment only after the Ruff, Pyright, Django/test, and package-build gates succeed. Pull requests from this repository and successful pushes to `develop` use the dedicated Vercel project named `q-bet`; the workflow never deploys with `--prod`.

Configure these GitHub repository values for the deployment workflow:

- secret `VERCEL_TOKEN`: a Vercel token with access to the Q-Bet team
- variable `VERCEL_ORG_ID`: the Vercel team/org identifier
- variable `VERCEL_PROJECT_ID`: the existing Q-Bet Vercel project identifier
- secret `QBET_DJANGO_SECRET_KEY`: a non-development Django secret used by hosted deployments

The existing `q-bet` Vercel project is targeted explicitly by its project/account identifiers. The existing portfolio Vercel project is not used or modified.

Hosted mode never uses writable SQLite as persistent Vercel storage. Without `QBET_DATABASE_URL`, the hosted shell remains read-only and only `/`, `/health/`, templates, and static assets are exposed. When a Supabase/PostgreSQL `QBET_DATABASE_URL` is configured and Django migrations have been applied, registration, login/logout, the basic authenticated dashboard, engine details, account boundary, and session-backed presentation/layout preferences may use persistent PostgreSQL state.

Routes that still depend on Q-Bet-specific SQLite repositories, including simulation/report persistence and internal monitoring surfaces, remain fail-closed with HTTP 503 until those repositories receive dedicated cloud adapters. Simulation/live execution remains disabled in hosted deployments.

The deployment job verifies the live preview with Vercel's authenticated curl command, including `/health/`, the public shell, and `/static/qbet_web/app.css`.

## Persistence Architecture

Q-Bet deliberately separates operational state from analytical workloads:

- **Supabase/PostgreSQL** is the cloud target for Django authentication, sessions, permissions, and later operational/ledger state.
- **SQLite** remains the transitional local default for operational repositories that have not yet migrated. Local Django can opt into PostgreSQL through `QBET_DATABASE_URL`.
- **DuckDB** is the local analytics/replay/backtesting store and must not become the Django authentication, session, provider-state, or ledger database.

`SUPABASE_QBET_TOKEN` is a Supabase Management API credential for automation/administration. It is not a PostgreSQL connection string. The hosted Django application requires `QBET_DATABASE_URL` from the Supabase project's Connect dialog, stored only in environment/secrets configuration. See [Django Web Shell](docs/django-web-shell.md) for bootstrap details.

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
