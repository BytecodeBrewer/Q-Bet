# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, controlled execution, and automated opportunity detection.

Q-Bet is a cloud-ready web application for a multi-engine quant betting portfolio. The first useful version should already have a complete matched-betting engine, a capital orchestrator prepared for more engines, data collection through Playwright/API adapters, bank connectivity, simulation mode, and controlled real execution.

## Current Status

**Stage 6 - Django web shell ready for review**:

- The Quant Engine source PDF is stored in [docs/Quant Engine_260820_114859.pdf](docs/Quant%20Engine_260820_114859.pdf).
- The expectation model and agent workflow documents exist.
- Typed, validated core models for events, markets, offers, opportunities, strategy results, and execution plans are implemented with focused unit tests.
- A deterministic qualifying-bet calculation now covers back/lay stakes, exchange commission, stake precision, liability limits, and both outcome P/L values.
- A deterministic free-bet conversion now supports stake-returned and stake-not-returned rules, exchange commission, stake precision, and both outcome P/L values.
- Stake rounding now evaluates permitted nearby increments and selects the legal plan with the strongest worst-case outcome, respecting liability, liquidity, and total-stake limits.
- A deterministic two-way arbitrage calculation now evaluates fee-adjusted odds, available liquidity, stake precision, allocation, and guaranteed P/L.
- A minimal Django web shell now serves a static engine-status page, health endpoint, local Django auth boundary, and privacy-preserving request correlation logs.
- Additional matched-betting strategies and orchestrator contracts are the next implementation steps.
- Supabase, CI/CD, UI, Playwright collectors, bank connector, and execution adapters are not implemented yet.

Update this section after every accepted ticket. It should always say where the project really is.

## Version 1 Target

Version 1 should include:

- a complete matched-betting engine from data intake to strategy result, execution plan, and report
- a capital orchestrator that can route capital and is ready for Yield and Alpha engines
- simulation mode for every engine, including placeholder/sandbox engines where not fully implemented
- controlled real execution for supported matched-betting workflows
- Playwright-based web collectors where APIs are unavailable or insufficient
- API adapter structure for crypto delta-neutral and prediction-market engines
- bank connectivity for balances and approved funding flows
- tests for calculations, strategy logic, mock integrations, and safety-critical workflows
- a compact web UI with main dashboard, engine views, simulation controls, and performance reports

The first UI does not need theme switching, drag-and-drop, or a polished bank cockpit. Useful beats decorative.

## Engine Portfolio

The Quant Engine PDF describes Q-Bet as a multi-engine system:

- **Base Engine:** matched betting, bookmaker/exchange arbitrage, free bets, odds boosts, dutching
- **Yield Engine:** crypto delta-neutral / funding-rate strategies through API adapters
- **Alpha Engine:** prediction-market making and arbitrage through API adapters
- **Capital Orchestrator:** allocates capital by EV, ROI, risk, liquidity, and capital lock-up

The Base Engine must be fully implemented first. Yield and Alpha should exist in sandbox/adapter form so the orchestrator can connect to them later without a rebuild.

## Core Architecture

```text
Data Sources
    -> Normalization
    -> Opportunity Engine
    -> Strategy Engine
    -> Risk / Capital Allocation
    -> Simulation and Execution Adapters
    -> Accounting and Reports
    -> Dashboard
```

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
- [Source PDF](docs/Quant%20Engine_260820_114859.pdf)
- [Django Web Shell](docs/django-web-shell.md)
