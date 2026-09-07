# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

Q-Bet has a working deterministic sports core around `BonusEngine` and `SportsCapitalEngine`. The calculation layer uses `Decimal` and covers qualifying bets, free bets, two-way arbitrage, multi-outcome dutching, dynamic rounding, fees, commission, tax handling, stake and liquidity limits, and liability. Normalized market-data contracts plus the `Sports Match Builder` validate provider-neutral snapshots before engine evaluation. Real market and result collectors are not connected yet; the current collector implementation remains deterministic and in-memory behind typed `DataCollector` and `ApiAdapter` contracts.

The internal Phase-2 pipeline is now connected across both operating modes. `WorkflowOrchestrator` handles correlated stage transitions while `ModeDispatchCoordinator` turns one eligible opportunity into durable, mode-isolated work items according to `RoutingConfiguration`. Simulation and Execution use separate PostgreSQL queues, histories, capital contexts, and result state. Mode-specific `RequestHandler` implementations perform deterministic pre-dispatch revalidation and result retrieval. `OperationalRiskLayer`, `LiquidityChecker`, `PortfolioLedger`, sandbox Execution, and explicit settlement boundaries are connected so both Simulation and Execution follow controlled capital lifecycles instead of mutating shared state. Simulation capital is reserved, locked, moved to pending, and settled through its own `PortfolioLedger`; Execution follows its own approval, dispatch, acknowledgement, and settlement lifecycle.

Supabase/PostgreSQL is the durable operational source of truth for Django auth and sessions, simulation control and reports, provider state, routing configuration, mode work queues, PortfolioLedgers, execution records, and administrator Monitoring records. DuckDB remains separate for runtime-local analytics, ordered replay, and backtesting workloads. SQLite is legacy-only and is no longer part of normal runtime persistence.

The Django application provides a dedicated home page, authentication, a customer-facing Execution dashboard, administrator-only Simulation controls, customer Reporting, and a separate administrator Monitoring plane. Customer report access is explicitly scoped per user. Monitoring now has its own persisted, redacted event model with correlation IDs and compact or extended administrator views. Staff can inspect technical workflow records independently from customer Reporting and export Monitoring data as CSV or JSON for a selected time range. The Execution side still uses sandbox/internal state only; real provider, bank, and market adapters belong to the next phase.

**Quality signals:** deterministic PostgreSQL-backed E2E scenarios cover single-mode and dual-mode routing, isolated Simulation/Execution state, revalidation, expiry and rescheduling, concurrent queue claims, cancellation, and idempotent settlement. Ruff, Pyright, pytest, Django checks, package builds, and preview smoke checks guard changes without defining the product itself.

**Still missing before Phase 2 is complete:**

- GUI-backed `WorkflowOrchestrator` / routing configuration so an administrator can activate or deactivate each engine and select Simulation, Execution, or both modes without changing backend configuration manually.
- customer-facing Reporting reduced to the intended minimal match-level projection: provider against provider, assigned amounts, match/result state, and creation time, with the main dashboard presenting the aggregated portfolio view without exposing internal pipeline or diagnostic metadata.
- the final engine status model in the main dashboard: **grey** when inactive, **green** when active without known problems, and **red** when active with warnings or errors. Warning and error symbols must remain distinguishable, and the expanded engine view must show safe, understandable incident descriptions without revealing internal architecture.
- full Monitoring coverage across every material pipeline source. The dedicated Monitoring store, compact/extended views, redaction, correlation filtering, and time-range exports exist; remaining work is to ensure routing, queues, `RequestHandler`, Domain Risk, Liquidity, ledger transitions, settlement, Simulation/Execution lifecycle, dependency failures, warnings, and internal errors are consistently emitted and presented in the Monitoring GUI.
- remaining GUI polish and restrained motion: clearer transitions between product areas, engine-state changes, progress/loading feedback, and subtle page or section animations without reducing readability or control.

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

## Persistence Architecture

Q-Bet deliberately separates durable operational state from runtime-local analytical workloads:

- **Supabase/PostgreSQL** is the durable shared source of truth for authentication, sessions, permissions, provider state, routing configuration, mode work queues, Simulation control/history, PortfolioLedgers, Execution lifecycle state, Reporting access, and Monitoring records. Local and hosted web instances reference this same database in normal use.
- **DuckDB** is the per-runtime analytics/replay/backtesting/fast-processing layer. A local Q-Bet process and a hosted runtime have separate DuckDB contexts. DuckDB state is not synchronized and must not be treated as durable shared storage.
- **SQLite** is legacy-only. It may be read by the one-time importer but is not part of normal application persistence.

Any analytical or simulation result that must survive process termination or be visible across local/hosted instances must be written back through the PostgreSQL operational boundary.

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
- a compact web UI with a customer-facing Execution dashboard, administrator-only Simulation and Monitoring controls, engine views, and performance reports

The current UI already supports a dedicated home page, theme/font preferences, session-scoped dashboard widget ordering, separate customer/admin surfaces, and deterministic sandbox control. Future visual polish should stay secondary to workflow safety, clear data boundaries, and useful operational behavior.

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
    -> Settlement
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
