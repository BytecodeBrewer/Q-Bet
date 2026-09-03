# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

**Stage 17 - Workflow simulation, provider-state persistence, and typed sports data preparation accepted**:

Q-Bet has the core of the first sports workflow in place. The agent workflow is documented, `BonusEngine` and `SportsCapitalEngine` are separated, and the main sports strategies are covered by deterministic calculation tests. This includes promotional matched betting, free bets, two-way arbitrage, dutching, stake optimization, rounding, fees, commission, liability, and the first operational risk checks.

The pipeline is taking shape from intake to simulation. Normalized market-data contracts define collected provider data, and the Sports Match Builder now validates typed bookmaker BACK/exchange LAY pairs from distinct providers plus exhaustive Dutching snapshot coverage before creating engine requests. From there, the engines calculate strategy results, domain risk can reject or recheck opportunities, and the concrete `LiquidityChecker` now handles proposal ranking, capital allocation, and workflow liquidity decisions.

The strongest end-to-end path today is simulation: evaluated sports opportunities can move through workflow transitions, risk checks, liquidity decisions, virtual-capital updates, reporting, and SQLite-backed report history without touching real execution. Provider state also has a typed SQLite persistence path, which gives the local version a practical bridge toward Supabase/PostgreSQL later.

The Django control and monitoring plane now provides a protected two-engine dashboard, engine detail and workflow views, persistent administrator-controlled simulation availability, a safe disable guard while simulation work is active, bounded GUI-started sandbox simulations for `BonusEngine` and `SportsCapitalEngine`, lifecycle status, session-scoped presentation settings, report history/detail selection, and CSV/JSON export from persisted simulation data. GUI-started simulations remain virtual-only and use the existing workflow-routed simulation path; they do not trigger live execution or real-money adapters. The broader gaps against the expectation model remain conformant external data/result adapters, bank sandbox/connectivity, Portfolio Ledger integration, and controlled live execution. The first bank-connector evaluation is complete; the bank layer remains mock-only until the account type and approved provider-onboarding route are chosen. `RequestHandler` refresh checks are now wired into the workflow at Domain Risk, Liquidity Check, and Dispatch, while concrete provider refresh clients remain future work. The math layer is solid, and Phase 2 is increasingly about making the connected local simulation path controllable, observable, and verifiable before external adapters are introduced.

## Local Web Setup

For the local Django shell, initialize Django's built-in authentication and session tables once before signing in:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Validation

The same baseline checks used by GitHub Actions can be run locally with:

```powershell
python -m pip install . -r requirements-dev.txt
python manage.py migrate --noinput
python -m pytest
python manage.py check
```

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
- a compact web UI with main dashboard, engine views, simulation controls, and performance reports

The first UI does not need theme switching, drag-and-drop, or a polished bank cockpit. Useful beats decorative.

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
