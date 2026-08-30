# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, workflow orchestration, liquidity checking prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

**Stage 16 - Workflow-routed simulation accepted; provider-state persistence ready for review**:
Q-Bet has the expectation model, agent workflow docs, typed domain models, and tested deterministic calculators for qualifying bets, free bets, two-way arbitrage, dutching, and stake rounding. `BonusEngine` and `SportsCapitalEngine` already produce approval-only execution plans. The workflow-routed simulation path now records correlation, domain-risk, liquidity, virtual-capital, and report data through the controlled pipeline while remaining isolated from real execution. Operational provider state now has a typed SQLite persistence seam, ready for review and later replacement by a Supabase/PostgreSQL adapter. The legacy `CapitalOrchestrator` still ranks proposals by EV, ROI, risk, liquidity, capital lock-up, and available capital; it should continue evolving toward the `LiquidityChecker` role from the pipeline architecture. Next up: data aggregation, Playwright/API adapters, bank connector, Supabase/CI/CD, richer GUI, and real execution adapters. Progress is real; finished product is still loading, please do not shake the machine.

Update this section after every accepted ticket. It should always say where the project really is.

## Local Web Setup

For the local Django shell, initialize Django's built-in authentication and session tables once before signing in:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
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
