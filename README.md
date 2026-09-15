# Q-Bet

> A modular Python quant platform for matched betting, sports arbitrage, simulation, controlled execution, and automated opportunity detection.

Q-Bet combines quantitative strategy logic with a persistent workflow platform. The project is designed around clear boundaries between calculation, risk, liquidity, Simulation, Execution, Monitoring, and Reporting so new engines and external integrations can be added without rebuilding the core system.

## Current Status

**v3.0 completes the deterministic internal platform.**

The current system includes:

- `BonusEngine` and `SportsCapitalEngine` with Decimal-based calculation logic for matched betting, arbitrage, dutching, fees, tax, rounding, stake and liability constraints
- a connected workflow from engine evaluation through risk, liquidity, Simulation or controlled Execution
- persistent PostgreSQL-backed workflow, approval, ledger, routing, reporting, monitoring, and notification state
- isolated Simulation and Execution capital/state boundaries
- explicit user approval before deterministic Execution dispatch
- restart-safe and replay-safe workflow handling with idempotent settlement
- administrator-controlled engine/mode routing through the Django GUI
- customer-facing Reporting separated from administrator-only technical Monitoring
- bounded CSV/JSON Monitoring exports and correlated workflow reconstruction
- connected end-to-end coverage for the complete GUI-to-workflow path
- a real read-only The Odds API adapter, provider-neutral market/result contracts, and a deterministic read-only bank-balance boundary
- a bunq bank adapter with explicit read-only and official sandbox modes, approval-gated fake-money sandbox payments, and opt-in E2E coverage
- approval-gated, idempotent Execution notifications with a replaceable Django email transport and deterministic no-network test adapter
- CI/CD with Ruff, Pyright, pytest, Django checks, package builds, PostgreSQL-backed tests, gated Vercel previews, and a separate protected bunq sandbox E2E workflow
- a dedicated Phase 3 performance baseline for connected workflow, PostgreSQL, Monitoring, web projection, and engine evaluation paths

Still under development are broader real-provider coverage, production bank onboarding, live execution integrations, additional engines, and the later multi-user/cloud execution model.

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

The GitHub Actions pipeline runs the same quality gates against an isolated PostgreSQL service. Provider-specific E2E workflows are opt-in and isolated from the normal offline validation path.

## Deployment

Pull requests and changes on `develop` can produce gated Vercel Preview deployments after CI passes.

The Vercel project receives its runtime configuration through environment variables, including `QBET_DATABASE_URL` and the Django secret. Preview environments keep high-authority behavior disabled unless explicitly enabled.

Day-to-day deployment and preview management is handled through the Vercel project UI and the GitHub Actions workflow; the README intentionally does not duplicate Vercel's own operating instructions.

## Execution Strategy

Q-Bet currently favors deterministic Simulation, sandbox adapters, explicit approvals, and controlled execution boundaries before increasing automation authority.

Live execution is intended to be introduced gradually with capital limits, time/risk constraints, provider-specific controls, and observable settlement behavior. API integrations are preferred where providers support them; notification-assisted workflows can bridge missing capabilities before browser automation becomes mature enough to take over more user-facing execution tasks.

Longer term, browser agents can extend the execution layer for providers that cannot be handled cleanly through APIs. Multi-user operation will require stronger account isolation, per-user execution contexts, and infrastructure-level separation before Q-Bet can be considered a broadly deployable shared platform.

## Architecture & Project Documents

The README stays intentionally high-level. The detailed design lives in the project documentation:

- [Expectation Model](docs/expectation-model.md) — product direction, phase boundaries, safety model, and long-term target state
- [Pipeline Architecture](docs/pipeline-architecture.md) — workflow structure, component responsibilities, and architecture diagrams
- [Phase 3 Integration Register](docs/phase-3-integration-register.md) — external integration work and implementation sequencing
- [Phase 3 Performance Baseline](docs/phase-3-performance-baseline.md) — connected internal performance measurements and scope
- [bunq Adapter](docs/bunq.md) — read-only/sandbox modes, secret configuration, and protected E2E execution
- [The Odds API](docs/the-odds-api.md) — current market-data adapter and development smoke path
- [Django Web Shell](docs/django-web-shell.md) — Django/Supabase bootstrap and operational notes

For implementation history and detailed changes, use the GitHub Issues, Pull Requests, and Releases.
