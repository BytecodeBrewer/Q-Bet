# Q-Bet Expectation Model

This document translates the Quant Engine PDF into a practical expectation model for code, tickets, agents, and review. The source PDF is stored in this folder and remains the north star for the product direction.

## Source Material

- Source PDF: [Quant Engine_260820_114859.pdf](Quant%20Engine_260820_114859.pdf)

Agents must read this expectation model first and use the PDF as deeper context when product direction is unclear.

## North Star

Q-Bet should become a modular quant platform that can:

- collect and normalize market, account, and bank data
- detect opportunities
- evaluate matched-betting and later multi-engine strategies
- simulate every engine
- execute supported matched-betting workflows with user-controlled boundaries
- allocate capital across engines
- report performance, actions, failures, and capital movement
- run as a cloud-hosted web application

The first usable version should not be only a toy simulator. It should be a working matched-betting product with simulation, reporting, orchestration, data collection, bank connectivity, and controlled execution.

## Engine Model From The PDF

The product direction is a multi-engine portfolio:

- **Base Engine:** matched betting, bookmaker/exchange arbitrage, free bets, odds boosts, dutching
- **Yield Engine:** crypto delta-neutral funding-rate strategies through API adapters
- **Alpha Engine:** prediction-market market making and cross-platform arbitrage through API adapters
- **Capital Orchestrator:** shared bankroll/capital manager that routes capital by expected value, ROI, risk, liquidity, and capital lock-up

The Base Engine must be implemented end to end first. Yield and Alpha engines should exist in sandbox/adapter form early enough that the orchestrator contract is real, but they do not need full production strategy logic in v1.

## MoSCoW

### Must

- Follow the direction of the Quant Engine PDF.
- Keep the architecture modular: data, normalization, opportunity detection, strategy, risk/capital, simulation, execution, reporting.
- Implement a complete matched-betting engine from normalized offers to strategy result, execution plan, account/report records, and UI visibility.
- Implement a capital orchestrator that can connect to multiple engines and route capital by EV, ROI, risk, liquidity, and capital lock-up.
- Provide data collection for all engine types:
  - Playwright collectors for web sources where API access is unavailable or insufficient
  - API adapter interfaces for crypto delta-neutral and prediction-market engines
  - mock providers for integration tests and sandbox runs
- Provide controlled real execution for supported matched-betting workflows, with approval boundaries where money movement or irreversible actions are involved.
- Provide simulation mode for all engines:
  - full simulation for the Base Engine
  - sandbox/placeholder simulation for Yield and Alpha engines
  - total starting capital
  - optional deposits/top-ups
  - selectable engine
  - selectable matched-betting strategies where relevant
  - visible progress while running
  - stop/end control at any time
  - final evaluation of all already executed simulation steps
  - persisted history/report entries
- Provide bank connectivity behind an interface so balances and approved funding flows can be supported.
- Never pull money from a bank account without explicit user approval. Returning money is allowed, but requests should still be logged.
- Implement matched-betting stake optimization, including dynamic rounding that preserves calculation quality while producing valid, practical stake sizes. (see for more Details in the source PDF !)
- Include account-operation risk controls for matched betting, such as warnings around unusual timing, niche-market exposure, withdrawal patterns, and strategy intensity. (see for more Details in the source PDF !)
- Provide a main dashboard with compact engine widgets:
  - status as traffic light
  - warning/error symbol when needed
  - details only after opening the engine view
- Provide separate views for each engine. Clicking a dashboard widget opens or enlarges the relevant engine view.
- Provide a performance dashboard for realized and simulated outcomes.
- Use Supabase as the preferred data platform unless a later ticket proves a better fit.
- Maintain tests for calculation logic, strategy evaluation, dynamic rounding, mock integrations, simulation flow, and execution safety boundaries.
- Add CI/CD expectations early: lint, test, build, and deployment checks.
- Keep ticket scope small enough that one change can be reviewed meaningfully.

### Should

- Provide first provider/service templates for known bookmakers, exchanges, banks, crypto venues, and prediction-market APIs, with clear notes that user credentials must be supplied later.
- Use Python for the engine and calculation layer.
- Use typed models for domain entities such as Event, Market, Offer, Opportunity, StrategyResult, ExecutionPlan, CapitalAllocation, SimulationRun, AccountAction, BankTransaction, and Report.
- Keep strategy providers pluggable.
- Model rounding, stake limits, fees, taxes, exchange commission, liquidity, and capital lock-up as explicit calculation inputs.
- Research regulated/open banking options before selecting the first real bank connector. Desired candidates include Revolut, ING, Commerzbank, and other viable providers.
- Treat PDF yield/profit numbers as hypotheses for simulation, not guaranteed returns.
- Keep the first web UI practical: main dashboard, engine views, simulation controls, reports, settings, and status.

### Could

- Add richer bank balance views and transaction breakdowns.
- Add detailed warning dashboards beyond the traffic-light status.
- Add configurable themes or visual design settings.
- Add drag-and-drop UI customization.
- Add performance tests over large historical simulation datasets.
- Add historical replay datasets.
- Add richer cloud observability and alert routing.

### Won't For Now

- No unmanaged full automation: runs are capped at 48 hours, and bank withdrawals/top-ups always require explicit user approval.

## Matched-Betting Strategy Requirements

The Base Engine must include:

- qualifying-bet calculation
- free-bet calculation
- arbitrage detection
- dutching
- dynamic rounding and stake optimization
- tax, fee, commission, liquidity, and stake-limit handling
- execution-plan generation
- result/report generation
- account-operation risk warnings

Dynamic rounding must be evaluated together with EV, liability, expected profit/loss, fees, taxes, and allowed stake increments. Rounding is only acceptable when the resulting plan remains mathematically sound.

Account-hygiene and Mug Betting are must have and see for more details what is required in the source PDF!

## Bank API Feasibility

The desired first candidates are Revolut or ING. Commerzbank and other banks can be considered.

Before choosing, create a research ticket that checks:

- whether an official API exists for personal or business accounts
- whether access is available in the user's country/account type
- authentication model
- sandbox availability
- transaction/balance access
- payment initiation/top-up support
- rate limits and costs
- compliance and data-retention obligations

Expected output: a short recommendation and a connector decision. Until then, the bank layer should be designed behind an interface and backed by mock data.

## Suggested First Milestones

1. Define typed domain models for markets, offers, opportunities, strategies, execution plans, capital, bank state, and simulation runs.
2. Implement matched-betting calculation tests for qualifying bets, free bets, arbitrage, dutching, taxes, fees, liquidity, and dynamic rounding.
3. Implement the Base Engine strategy pipeline and execution-plan generation.
4. Implement the Capital Orchestrator contract with sandbox Yield and Alpha engine adapters.
5. Add mock providers and simulation loops for all engines.
6. Add controlled execution adapter interfaces and the first matched-betting execution template.
7. Persist simulation runs, execution plans, account actions, and reports in Supabase or a local interim store.
8. Build the first dashboard around engine status, simulation controls, and reports.
9. Research bank API feasibility and implement the selected bank connector behind the banking interface.

## Definition of Done

A ticket is done when:

- its acceptance criteria pass
- tests relevant to the changed behavior exist and pass
- README Current Status is updated when project progress changed
- architecture docs are updated when contracts or direction changed
- the change is small enough to review without archaeology
- money movement and execution boundaries are explicit when the ticket touches bank or execution code

## Product Bias

Build the working matched-betting product first, with simulation and controlled execution. Keep the other engines connected through real contracts and sandbox adapters so the system grows without being rebuilt from scratch.
