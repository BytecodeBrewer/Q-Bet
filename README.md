# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

**Stage 17 - Workflow simulation, provider-state persistence, and typed sports data preparation accepted**:

Q-Bet has the core of the first sports workflow in place. The agent workflow is documented, `BonusEngine` and `SportsCapitalEngine` are separated, and the main sports strategies are covered by deterministic calculation tests. This includes promotional matched betting, free bets, two-way arbitrage, dutching, stake optimization, rounding, fees, commission, liability, and the first operational risk checks.

The pipeline is taking shape from intake to simulation. Normalized market-data contracts define collected provider data, and the Sports Match Builder now validates typed bookmaker BACK/exchange LAY pairs from distinct providers plus exhaustive Dutching snapshot coverage before creating engine requests. From there, the engines calculate strategy results, domain risk can reject or recheck opportunities, and the concrete `LiquidityChecker` now handles proposal ranking, capital allocation, and workflow liquidity decisions.

The strongest end-to-end path today is simulation: evaluated sports opportunities can move through workflow transitions, risk checks, liquidity decisions, virtual-capital updates, reporting, and SQLite-backed report history without touching real execution. Provider state also has a typed SQLite persistence path, which gives the local version a practical bridge toward Supabase/PostgreSQL later.

The Django GUI now keeps the user-facing Execution plane and the internal Simulation plane explicitly separate. Normal authenticated users see only the two-engine Execution dashboard; simulation history no longer feeds or alters its totals. Staff users can enable an additional, clearly labelled Simulation dashboard from the privileged Admin Area inside Settings. That admin-only plane reuses the existing deterministic `BonusEngine` and `SportsCapitalEngine` simulation path, virtual capital, lifecycle state, persisted reports, and CSV/JSON exports without touching live execution or real-money adapters. Current simulation reports and internal monitoring remain staff-only. Dashboard engine widgets can be reordered independently inside the Execution and Simulation planes, with session-scoped layout persistence so widgets cannot be dragged across the two state domains. Presentation theme and font-size preferences remain session-scoped as well.

The broader gaps against the expectation model remain conformant external data/result adapters, bank sandbox/connectivity, Portfolio Ledger integration, customer-facing reporting separated from internal monitoring, and controlled live execution. The first bank-connector evaluation is complete; the bank layer remains mock-only until the account type and approved provider-onboarding route are chosen. `RequestHandler` refresh checks are now wired into the workflow at Domain Risk, Liquidity Check, and Dispatch, while concrete provider refresh clients remain future work. The math layer is solid, and Phase 2 is increasingly about making the connected local simulation path controllable, observable, and verifiable before external adapters are introduced.

## Local Web Setup

For the local Django shell, initialize Django's built-in authentication and session tables once before signing in:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

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

Configure these GitHub repository values before the first deployment run:

- secret `VERCEL_TOKEN`: a Vercel token with access to the Q-Bet team
- variable `VERCEL_ORG_ID`: the Vercel team/org identifier
- secret `QBET_DJANGO_SECRET_KEY`: a non-development Django secret used only by the hosted preview

The first successful deployment job creates the dedicated `q-bet` Vercel project when it is missing and then links the CI workspace to it. The existing portfolio Vercel project is not used or modified.

The hosted preview is intentionally a read-only deployment proof, not production Q-Bet. Vercel Functions do not provide persistent local SQLite storage, so hosted mode disables the SQLite-backed simulation/report store, uses no writable Django SQLite database, and fails persistence-dependent auth/control/report/simulation routes closed with HTTP 503. The public `/` shell, `/health/`, templates, and static assets remain available for deployment smoke testing. Local development keeps the full SQLite-backed behavior.

The deployment job verifies the live preview with Vercel's authenticated curl command, including `/health/`, the public shell, and `/static/qbet_web/app.css`.

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