# Q-Bet Expectation Model

This document translates the Quant Engine PDF into a practical expectation model for code, tickets, and review. The PDF remains the product north star; this file is the working contract.

## North Star

Q-Bet should become a modular quant platform that can:

- collect and normalize market/account data
- detect opportunities
- evaluate strategies
- simulate strategy behavior
- allocate capital across engines
- report performance and failures
- support cloud operation as a web application

The system should move quickly toward a usable product, then improve through simulations, performance tests, and measured iteration.

## Engine Model From The PDF

The product direction is a multi-engine portfolio:

- **Base Engine:** matched betting, bookmaker/exchange arbitrage, free bets, odds boosts, dutching
- **Yield Engine:** crypto delta-neutral funding-rate strategies using spot/perpetual positions
- **Alpha Engine:** prediction-market market making and cross-platform arbitrage
- **Capital Orchestrator:** shared bankroll/capital manager that routes capital by expected value, ROI, risk, and capital lock-up

The Base Engine and simulation layer come first. Yield and Alpha engines should be designed as future plug-ins, not hard-coded into the first implementation.

## MoSCoW

### Must

- Follow the direction of the Quant Engine PDF.
- Keep the architecture modular: data, normalization, opportunity detection, strategy, risk/capital, simulation/execution, reporting.
- Start with a simulation-first workflow before real execution.
- Provide a simulation mode with:
  - total starting capital
  - optional deposits/top-ups
  - selectable engine
  - selectable matched-betting strategies where relevant
  - visible progress while running
  - stop/end control at any time
  - final evaluation of all already executed simulation steps
  - persistent history/report entries later
- Provide a main dashboard with compact engine widgets:
  - capital/volume inside each engine
  - number of actions/orders
  - alerts and errors
  - traffic-light status
- Provide separate views for each engine.
- Provide a bank/account view showing account-related activity.
- Provide a performance dashboard.
- Use Supabase as the preferred data platform unless a later ticket proves a better fit.
- Maintain tests for calculation logic, strategy evaluation, and critical workflows.
- Add CI/CD expectations early: lint, test, build, and deployment checks.
- Keep ticket scope small enough that one change can be reviewed meaningfully.

### Should

- Use Python for the engine and calculation layer.
- Use typed models for domain entities such as Event, Market, Offer, Opportunity, StrategyResult, CapitalAllocation, SimulationRun, and Report.
- Keep strategy providers pluggable.
- Keep UI views practical and dense enough for repeated use.
- Include risk warnings for aggressive matched-betting settings, especially where behavior may increase account, platform, or terms-of-service risk.
- Research regulated/open banking options before selecting a bank connector.
- Treat PDF yield/profit numbers as hypotheses for simulation, not guaranteed returns.
- Model rounding, stake limits, fees, taxes, and liquidity as explicit calculation inputs.

### Could

- Add Revolut, ING, Commerzbank, or another banking connector after a feasibility ticket.
- Add prediction-market and yield-style engines once matched-betting simulation is stable.
- Add performance tests once simulations run over meaningful datasets.
- Add historical replay datasets.
- Add configurable themes/design settings.
- Add Playwright collectors for permitted research/navigation flows.
- Add REST/WebSocket adapters for markets where official APIs allow it.

### Won't For Now

- No VPN rotation.
- No identity rotation.
- No IBAN/payment-account rotation to bypass platform controls.
- No stealth automation intended to evade anti-fraud or account-limit systems.
- No direct real-money execution before simulation, reporting, permissions, and risk controls are mature.
- No memecoin sniping or pump-and-dump execution in the first product scope.

## Bank API Feasibility

The desired first candidates are Revolut or ING. Commerzbank and other banks can be considered.

Before choosing, create a research ticket that checks:

- whether an official API exists for personal or business accounts
- whether access is available in the user's country/account type
- authentication model
- sandbox availability
- transaction/balance access
- rate limits and costs
- compliance and data-retention obligations

Expected output: a short recommendation and a connector decision. Until then, the bank layer should be designed behind an interface and backed by mock data.

## Suggested First Milestones

1. Define typed domain models for markets, offers, opportunities, strategies, capital, and simulation runs.
2. Implement matched-betting calculation tests for qualifying bets and free bets.
3. Add a mock data provider and first simulation loop.
4. Persist simulation runs and reports in a simple local store, then Supabase.
5. Build the first dashboard view around simulation status and reports.
6. Research bank API feasibility before implementing a real connector.

## Definition of Done

A ticket is done when:

- its acceptance criteria pass
- tests relevant to the changed behavior exist and pass
- the README Current Status is updated when project progress changed
- architecture docs are updated when contracts or direction changed
- the change is small enough to review without archaeology

## Product Bias

Move toward a working, observable, simulated product first. Perfect comes later, preferably after the software has earned the right to be complicated.
