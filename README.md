# Q-Bet

> A modular Python quant platform for matched betting, sports arbitrage, simulation, controlled execution, and automated opportunity detection.

Q-Bet combines quantitative strategy logic with a persistent workflow platform. The project is designed around clear boundaries between calculation, risk, liquidity, Simulation, Execution, Monitoring, and Reporting so new engines and external integrations can be added without rebuilding the core system.

## Current Status

Q-Bet is a cloud-backed Python/Django application with PostgreSQL/Supabase as its durable operational backend and Vercel as the web deployment layer. The web application already exposes user and administrator capabilities on top of persistent workflow, ledger, reporting, monitoring, simulation, approval, and notification state. Local development and validation run against isolated PostgreSQL environments, while CI/CD covers the automated quality gates and deployment checks.

The core system is structurally available from data ingestion to user-facing output. `SportsCapitalEngine` has a connected fixed-odds sportsbook Simulation path through The Odds API and the Sports Match Builder. `BonusEngine` now has a promotion-aware fixed-odds sportsbook Simulation path for the supported qualifying-bet and free-bet strategies: user-owned Bonus Offers provide promotion terms, the versioned GGL-backed catalog validates sportsbook identity, and fresh The Odds API data provides current opposing quotations for two-outcome markets. Unsupported promotion conditions, missing provider/account risk state, missing explicit provider fee/tax terms, and incompatible or stale market data fail closed. Legacy exchange-hedged calculators and fixtures remain only for regression compatibility, while `SportsExchangeEngine` stays a separate planned peer-to-peer exchange engine. Capital movements now use an explicit requirement → attention → approval → pending → reconciliation lifecycle; manual or bunq-sandbox acknowledgement alone never changes authoritative `PortfolioLedger` balances. Confirmed reconciliation is the boundary that applies funding or withdrawal state. Controlled post-event settlement, Monitoring, Reporting, notifications, and approval boundaries remain part of the application.

Users can persist personal `BonusEngine` / `SportsCapitalEngine` Simulation and Execution selections inside the global staff routing guardrails. Missing preferences fail closed, globally disabled routes remain unavailable without deleting stored user intent, and Execution selection does not bypass approval or capital boundaries.

Smart Polling now has restart-safe PostgreSQL work state and a protected bounded hosted tick for the connected SportsCapital Simulation / The Odds API route. Supabase Cron is a wake-up mechanism only: the Python `SmartPollingPolicy` remains authoritative for freshness, timing, capacity, terminal state, and whether a provider request is actually made. Disabling a route stops future polling without deleting its durable history.

Engine runtime state is explicit: `Inactive` means routing is disabled, `Ready` means the engine is enabled and ready to accept work, and `Running` is reserved for durable work that is actually processing. Monitoring, engine detail, and the dashboard use the same persisted state sources and vocabulary for that distinction.

Display preferences are durable per user. Language affects the supported authenticated application chrome and locale-sensitive date/number formatting. The currency setting is a non-converting **preferred recorded currency**: Q-Bet prioritizes and highlights report sections whose authoritative values are already recorded in that currency. It never converts or relabels transaction/report amounts without an explicitly approved FX presentation source.

The current operating direction is API- and notification-first. Additional engines, broader live execution, and browser-based execution are deliberately not part of the current scope; the focus is on connecting the existing components into complete, realistic system flows and hardening those paths before expanding the product surface.

> [!NOTE]
> Q-Bet is currently in Phase 3. The remaining integration work and Definitions of Done are tracked in [Phase 3 Missing Points](docs/phase-3-missing-points.md).

## Engine Portfolio

| Component | Status | Focus |
| --- | --- | --- |
| `BonusEngine` | ✅ Implemented | Matched betting, qualifying bets, free bets, promotions |
| `SportsCapitalEngine` | ✅ Implemented | Sports arbitrage, dutching, real-capital strategies |
| `TicketEngine` | ⏳ Planned | Event-driven secondary ticket arbitrage |
| `PredictionMarketEngine` | ⏳ Planned | Prediction-market and order-book strategies |
| `CryptoYieldEngine` | ⏳ Planned | Delta-neutral and funding-rate strategies |
| `MLEdgeLayer` | ⏳ Planned | Fair-odds support, value detection, drift analysis |

## Tech Stack

| Area | Technology |
| --- | --- |
| Application | Python, Django |
| Operational database | PostgreSQL / Supabase |
| Analytics & replay | DuckDB |
| Web deployment | Vercel |
| CI/CD | GitHub Actions |
| Quality gates | Ruff, Pyright, pytest, Django checks |

Supabase/PostgreSQL is the durable operational source of truth. DuckDB is intentionally separate and used for analytical, replay, and backtesting workloads.

## Running Q-Bet

For local development, use the PostgreSQL connection string from the Supabase project and expose it as `QBET_DATABASE_URL`.

```powershell
$env:QBET_DATABASE_URL='postgresql://...'
python manage.py migrate
python manage.py runserver
```

Create the initial administrator when needed with:

```powershell
python manage.py createsuperuser
```

User, group, staff, and permission management then lives in Django Admin.

> Legacy SQLite import support still exists for older project data, but SQLite is not part of the current runtime architecture.

## Validation

Tests must use an isolated PostgreSQL database rather than the shared Supabase runtime database.

```powershell
$env:QBET_TEST_DATABASE_URL='postgresql://qbet:qbet@127.0.0.1:5432/qbet_test'
python -m pip install . -r requirements-dev.txt
python -m ruff check .
python -m pyright
python -m pytest
python manage.py check
python -m build
```

The GitHub Actions **PostgreSQL validation gate** is the authoritative standard result for Dev Handoffs and reviews. For pull requests it explicitly checks out and verifies the submitted PR head SHA, rather than GitHub's synthetic merge SHA; pushes verify their own push SHA. Every run receives a fresh PostgreSQL 16 service and runs `git diff --check`, Ruff, Pyright, migration checks, migrations, the full standard Pytest suite, Django checks, and the package build. The gate receives no provider or bank credentials; external bunq sandbox E2E remains in its separate opt-in workflow, and the performance baseline remains a separate job.

## Deployment

Pull requests run a Vercel Preview build check against the exact PR head after the normal validation and performance jobs. It verifies that the app can be built, but does not create a deployment for feature branches. A push to `develop` creates and health-checks a Vercel Preview deployment. Only a push to `main` builds and deploys to the Production Vercel environment and checks the stable Q-Bet URL.

Production and Preview configuration, including runtime secrets, live in the corresponding Vercel environments. GitHub Actions does not connect to the application database or copy secrets out of Supabase during deployment. Deployments do not run schema migrations or gate on migration state; schema changes remain an explicit operator-controlled task.

Day-to-day deployment management is handled through the Vercel project UI and the GitHub Actions workflow; the README intentionally does not duplicate Vercel's own operating instructions.

## Execution Strategy

Q-Bet currently favors deterministic Simulation, sandbox adapters, explicit approvals, and controlled execution boundaries before increasing automation authority.

Live execution is intended to be introduced gradually with capital limits, time/risk constraints, provider-specific controls, and observable settlement behavior. API integrations are preferred where providers support them; notification-assisted workflows can bridge missing capabilities before browser automation becomes mature enough to take over more user-facing execution tasks.

Longer term, browser agents can extend the execution layer for providers that cannot be handled cleanly through APIs. Multi-user operation will require stronger account isolation, per-user execution contexts, and infrastructure-level separation before Q-Bet can be considered a broadly deployable shared platform.

## Architecture & Project Documents

The README stays intentionally high-level. The detailed design lives in the project documentation:

- [Expectation Model](docs/expectation-model.md) — product direction, phase boundaries, safety model, and long-term target state
- [Pipeline Architecture](docs/pipeline-architecture.md) — workflow structure, component responsibilities, and architecture diagrams
- [Phase 3 Missing Points](docs/phase-3-missing-points.md) — prioritized completion plan, current gaps, E2E definitions, and Definitions of Done
- [Phase 3 Integration Register](docs/phase-3-integration-register.md) — concrete external integration choices and implementation status
- [Phase 3 Performance Baseline](docs/phase-3-performance-baseline.md) — connected internal performance measurements and scope
- [Phase 3 Hosted Web Performance](docs/phase-3-hosted-performance.md) — Vercel/Supabase hosted-path measurements and repeatable profiling procedure
- [bunq Adapter](docs/bunq.md) — read-only/sandbox modes, secret configuration, and protected E2E execution
- [The Odds API](docs/the-odds-api.md) — current market-data adapter and development smoke path
- [Django Web Shell](docs/django-web-shell.md) — Django/Supabase bootstrap and operational notes

For implementation history and detailed changes, use the GitHub Issues, Pull Requests, and Releases.
