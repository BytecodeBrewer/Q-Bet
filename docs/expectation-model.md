# Q-Bet Expectation Model

This document is the authoritative expectation model for Q-Bet code, tickets, agents, and review. It is the single working source for product direction, architecture, roadmap, and agent decisions.

## Source Of Truth

Agents must use this Markdown file as the working source of truth for product direction, architecture, roadmap, and review.

## North Star

Q-Bet is a deterministic, mathematically protected multi-asset quant platform. It identifies and evaluates market inefficiencies, promotional opportunities, arbitrage situations, and yield mechanisms across sports betting, event tickets, crypto delta-neutral strategies, and prediction markets.

The system should eliminate directional risk where possible, or operate only under clearly positive expected value where risk remains. It must expose decisions through a full GUI, keep mathematical logic isolated from execution and persistence, and protect capital through explicit risk and liquidity controls.

Core principles:

- **Zero-CLI / Full GUI First:** End users should not need a console. Simulation, execution approval, settings, logs, exports, and analysis belong in the GUI.
- **Decoupled engines via Protocol:** Engines are autonomous modules with typed request/result contracts.
- **Domain and pipeline separation:** Pure formulas do not know live balances, execution adapters, providers, or GUI state.
- **Capital safety:** The orchestrator protects against insolvency, over-allocation, liquidity gaps, and user-side capital changes.
- **Small implementation tickets:** Build quickly, but keep reviewable slices.

## Four-Layer Pipeline

Q-Bet follows a strict Clean Architecture style pipeline:

```text
Layer 1: Strategy Engines (Pure Math)
    Calculates EV, stakes, liability, P/L, returns, and raw plans.

Layer 2: Verification & Operational Risk
    Checks provider state, account state, cooldowns, rounding policy, market filters,
    exposure thresholds, and operational warnings.

Layer 3: Capital Orchestrator & Safety Sentinel
    Allocates bankroll, reserves capital, checks thresholds, ranks candidates,
    and stops new proposals when capital safety is violated.

Layer 4: Execution & Simulation Layer (GUI-Bound)
    Handles user approvals, sandbox simulation, execution adapters, logs,
    reports, exports, and dashboard state.
```

Important separation rule: Layer 1 produces mathematically valid strategy outputs. It must not read databases, accounts, sessions, live balances, browser profiles, GUI state, or execution state. Layers 2-4 decide whether, when, and how a valid plan may be simulated, presented, or executed.

## Engine And Strategy Matrix

| Tier / Category | Engine Name | Primary Mechanism | Roadmap Target |
| --- | --- | --- | --- |
| Base / Promo Engine | `BonusEngine` | Matched betting with qualifying bets and free bets | Version 1.0 |
| Sports Capital Engine | `SportsCapitalEngine` | Sports arbitrage and multi-outcome dutching | Version 1.0 |
| Crypto Yield Engine | `CryptoYieldEngine` | Delta-neutral funding-rate arbitrage | Version 2.0 |
| Prediction Engine | `PredictionMarketEngine` | Polymarket/Kalshi market making and latency arbitrage | Version 3.0 |
| Ticket Engine | `TicketEngine` | Event-driven secondary ticket arbitrage | Version 3.0 |
| ML Edge Layer | `MLEdgeLayer` | Statistical value betting and drift detection | Version 3.0 |

### Base Tier: Promotional And Low-Risk Cashflow

- **Qualifying Bet Matched Betting:** Hedge qualifying bookmaker bets with minimal mathematical loss to unlock bonus value.
- **SNR Free Bets:** Convert stake-not-returned free bets into cash value; target conversion should be measured and simulated.
- **SR Free Bets:** Convert stake-returned free bets where the promotional stake is returned on a win.
- **Reload and Cashback Harvesting:** Handle recurring promotional offers for existing users.
- **Fintech and Depot Promo Harvesting:** Track and evaluate broker, neobank, and crypto-exchange promotional bonuses as a later adjacent module.

### Yield Tier: Real-Capital Arbitrage And Systemic Yields

- **Two-Way and Multi-Outcome Arbitrage:** Exploit odds differences across providers to target guaranteed positive return.
- **Multi-Bookmaker Dutching:** Split stake across all possible outcomes of a market without requiring a betting exchange.
- **Middle Betting and Asian Handicap Spreads:** Evaluate overlapping payout ranges, such as Over 2.25 versus Under 2.75.
- **Event-Driven Secondary Ticket Arbitrage:** Later module for buying high-demand event tickets at face value and reselling on secondary markets with a target margin.
- **Crypto Delta-Neutral Funding Rate Arbitrage:** Hold spot and opposing perpetual/futures exposure to target funding-rate yield while reducing price-direction exposure.

### Alpha Tier: Probabilistic Edge And Market Making

- **Value Betting via Sharp-Market Benchmark:** Compare consumer bookmaker odds against margin-adjusted fair odds from sharp markets and use fractional Kelly sizing.
- **Prediction Market AMM:** Place bid/ask orders around fair value on prediction markets and measure spread/reward capture.
- **Cross-Platform Latency Arbitrage:** Compare delayed prices across supported prediction-market venues where official APIs allow access.
- **Statistical Predictive Modeling:** Build fair odds and drift models with methods such as Poisson regression, Dixon-Coles, xG-style features, or XGBoost.

## Version Roadmap

### Version 1.0: Foundation, Full GUI, Simulation And Controlled Execution

Version 1.0 delivers the operational MVP for promo and sports-betting workflows with transparency and user control.

Must include:

- Full GUI operation with no required CLI for end users.
- Visual execution-order approval through `[Approve Order]` / `[Reject]` style prompts.
- No real-money execution without explicit GUI approval.
- Live-market data can be used in sandbox simulation, but simulation execution must remain isolated from real execution.
- Simulation and live execution cannot run at the same time in v1 if they share state.
- A simulation orchestrator with virtual capital, configurable starting capital, optional top-ups, selectable engines, visible progress, stop/end control, and persisted reports.
- GUI-integrated logs, reports, and CSV/JSON exports for calculations, odds history, simulation logs, and cashflow analysis.
- `BonusEngine` for qualifying bets and SNR/SR free bets.
- `SportsCapitalEngine` for two-way arbitrage and two-to-four-outcome dutching.

### Version 2.0: Crypto Yield, Advanced Safety Orchestrator And Risk Profiles

Version 2.0 expands the portfolio with crypto delta-neutral yield and stronger safety controls.

Should include:

- `CryptoYieldEngine` for spot/perpetual funding-rate strategies.
- GUI risk profiles such as Conservative, Balanced, and Aggressive.
- Risk profiles controlling minimum margins, maximum stake per opportunity, and rounding aggressiveness.
- Continuous account and liquidity monitoring across bookmakers, exchanges, banks, and wallets.
- Multi-tier threshold system:
  - Green: normal operation.
  - Yellow: warning, such as unusual withdrawal size or failed deposits.
  - Red: critical liquidity condition.
- Emergency stop behavior:
  - stop generating new orders on red threshold or major capital withdrawal
  - finish already running transactions in an orderly way
  - run rebalance and liquidity checks afterward
  - pause execution if capital remains below the red threshold

### Version 3.0: Prediction Markets, Ticket Arbitrage, Multi-User And ML

Version 3.0 turns Q-Bet into a broader multi-user quant platform.

Could include:

- `PredictionMarketEngine` for market making, arbitrage, and liquidity rewards.
- `TicketEngine` for event-driven ticket arbitrage.
- Multi-tenant architecture with isolated bankrolls, API keys, roles, and permissions.
- Cryptographic audit logging for system actions, security events, and financial evaluations.
- ML edge layer for fair-odds models, value betting, drift detection, and market-inefficiency detection.

## Five-Phase Scaling Roadmap

| Phase | Capital Level | Enabled Modules | Primary Mechanism | Target Cashflow |
| --- | --- | --- | --- | --- |
| Phase 1 | EUR 100-500 | `BonusEngine` + Fintech Harvesting | New-user bonuses, promo cashflow, depot promos | EUR 300-600 / month |
| Phase 2 | EUR 500-2,000 | `SportsCapitalEngine` | Arbitrage, reloads, odds boosts, real-capital matched betting | EUR 600-1,000 / month |
| Phase 3 | EUR 2,000-5,000 | `TicketEngine` | Automated ticket sourcing and secondary-market margin | EUR 800-1,400 / month |
| Phase 4 | EUR 5,000-10,000 | `PredictionMarketEngine` | Market making and order-book arbitrage | EUR 1,200-2,000 / month |
| Phase 5 | EUR 10,000+ | `CryptoYieldEngine` | Delta-neutral funding-rate arbitrage | EUR 1,800-3,500+ / month |

These numbers are planning hypotheses for simulation, not guaranteed returns.

## Storage And Persistence Model

| Layer | Storage Type | Necessity | Reason |
| --- | --- | --- | --- |
| Layer 1: Math | Stateless / in-memory | No DB storage | Pure calculations must remain isolated and deterministic. |
| Layer 2: Verification | In-memory cache backed by SQLite or later Supabase/Postgres | Required | Stores provider/account state, cooldowns, active bet counts, last action timestamps, market filters, and warning state. |
| Layer 3: Capital | ACID ledger in SQLite first, Supabase/Postgres later | Critical | Central authority for bankroll, balances, reserved funds, thresholds, and race-condition protection. |
| Layer 4: Execution/GUI | SQLite/Supabase plus filesystem or object storage for exports | Required | Stores execution history, GUI session state, simulation results, reports, and CSV/JSON exports. |

The local implementation may start with SQLite. The cloud target should use Supabase/Postgres unless a later technical decision proves a better fit.

## Formula And Calculation Standards

All calculations must be deterministic and should use `Decimal` for money-sensitive arithmetic. NumPy may be introduced for vectorized analysis or simulations where precision boundaries are explicit.

Required formula standards:

- Qualifying Bet Lay Stake: `L = (B * O_b) / (O_l - c)`.
- SNR Free Bet Lay Stake: `L_SNR = (B * (O_b - 1)) / (O_l - c)`.
- Value Bet EV: compare offered odds against margin-adjusted fair probability, then size using fractional Kelly when enabled.

Where:

- `B` = back stake at bookmaker
- `O_b` = bookmaker back odds
- `O_l` = exchange lay odds
- `c` = exchange commission rate, e.g. `0.02` for 2 percent
- `p_sharp` = implied fair probability from a sharp or exchange benchmark after margin removal

Calculations must model stake precision, rounding, fees, taxes, exchange commission, liquidity, stake limits, total-stake limits, liability, and capital lock-up explicitly.

## MoSCoW

### Must

- Follow this expectation model as the primary source of truth.
- Keep architecture modular across the four layers.
- Implement `BonusEngine` and `SportsCapitalEngine` as distinct v1 engines, even if they share calculators.
- Keep Yield, Alpha, Ticket, and ML modules behind explicit contracts until their production logic is built.
- Implement a capital orchestrator that can connect to multiple engines and route capital by EV, ROI, risk, liquidity, and capital lock-up.
- Provide data collection contracts for all engine types:
  - Playwright collectors for permitted web data collection where API access is unavailable or insufficient
  - API adapter interfaces for crypto yield and prediction-market engines
  - mock providers for integration tests and sandbox runs
- Provide controlled real execution for supported v1 workflows, with user approval boundaries for money movement or irreversible actions.
- Provide full simulation mode for v1 engines and sandbox simulation for later engines.
- Provide bank connectivity behind an interface for balances and approved funding flows.
- Never pull money from a bank account without explicit user approval.
- Implement matched-betting stake optimization, including dynamic rounding that preserves calculation quality while producing valid practical stake sizes.
- Implement Layer 2 operational risk checks for provider limits, cooldowns, market/liquidity filters, exposure thresholds, timing/pacing rules, withdrawal warnings, and strategy intensity.
- Provide a main dashboard with compact engine widgets showing traffic-light status plus warning/error symbols.
- Provide separate views for each engine; clicking a widget opens or expands the relevant engine view.
- Provide visual order approval and rejection in the GUI.
- Provide performance reports for realized and simulated outcomes.
- Provide GUI-integrated CSV/JSON export for calculation, odds, simulation, and cashflow records.
- Maintain tests for calculation logic, strategy evaluation, dynamic rounding, mock integrations, simulation flow, execution safety boundaries, and capital allocation.
- Add CI/CD checks for lint, tests, build, and deployment readiness.
- Keep tickets small enough for meaningful review.

### Should

- Provide first provider/service templates for known bookmakers, exchanges, banks, crypto venues, and prediction-market APIs, with clear notes that user credentials must be supplied later.
- Use Python 3.12+ and Pydantic v2 for engine/domain models.
- Use typed models for Event, Market, Offer, Opportunity, StrategyResult, ExecutionPlan, CapitalAllocation, SimulationRun, AccountAction, BankTransaction, ProviderState, VerificationResult, Bankroll, AccountBalance, Thresholds, and Report.
- Keep strategy providers pluggable behind protocols.
- Use GUI risk profiles to tune risk thresholds, allowed stakes, margin requirements, and rounding aggressiveness.
- Research regulated/open banking options before selecting the first real bank connector. Desired candidates include Revolut, ING, Commerzbank, and other viable providers.
- Treat yield/profit numbers in this expectation model as simulation hypotheses, not promises.
- Keep the first UI practical: main dashboard, engine views, simulation controls, reports, settings, and status.

### Could

- Add richer bank balance views and transaction breakdowns.
- Add detailed warning dashboards beyond traffic-light status.
- Add configurable themes or visual design settings.
- Add drag-and-drop UI customization.
- Add performance tests over large historical simulation datasets.
- Add historical replay datasets.
- Add richer cloud observability and alert routing.
- Add multi-user tenant isolation once single-user flows are stable.

### Won't For Now

- No unmanaged full automation: unattended runs are capped at 48 hours.
- No bank withdrawals/top-ups without explicit user approval.

## Matched-Betting Strategy Requirements

The v1 sports-betting system is split into two engines:

- `BonusEngine`: qualifying bets, SNR/SR free bets, reload/cashback offers, bonus-condition tracking, and promo conversion reports.
- `SportsCapitalEngine`: two-way arbitrage, multi-outcome dutching, odds boosts, real-capital matched-betting opportunities, and later value-betting candidates.

Both engines may share pure calculation primitives, but their request/result models, reporting categories, risk checks, and GUI views must remain distinct.

The matched-betting stack must include:

- qualifying-bet calculation
- SNR and SR free-bet calculation
- arbitrage detection
- two-to-four-outcome dutching
- dynamic rounding and stake optimization
- tax, fee, commission, liquidity, stake-limit, and total-stake handling
- execution-plan generation
- result/report generation
- account-operation risk warnings

Dynamic rounding must evaluate permitted floor/ceiling candidates at configured stake increments against final outcome values. The selected plan must maximize the least favorable eligible outcome while respecting liability, liquidity, total-stake limits, and configured strategy tolerance. Rounding is only acceptable when the resulting plan remains mathematically sound.

Operational risk checks must not alter pure math. They run after strategy calculation and before capital allocation/execution. They should emit structured statuses, warnings, required approvals, or rejections.

Blueprint operational-risk categories to represent safely in code:

- stake rounding policy and allowed deviation thresholds
- account activity pacing and strategy intensity limits
- market/liquidity filtering and niche-market restrictions
- timing windows and delay/pacing rules for operational safety
- max exposure and provider-limit usage thresholds
- withdrawal cadence warnings and capital-cycle management
- browser/session separation as credential/session-safety metadata, not evasion tooling

The `OperationalRiskLayer` processes domain-specific execution policies in Python 3.12+ prior to capital allocation:

### A. Pre-Match Liquidity & Kickoff Proximity Scheduling

- **Event Timing Window:** Orders must be evaluated and scheduled close to event kickoff (typically $T-15$ to $T-5$ minutes). This maximizes market volume, minimizes odds drift, and aligns execution with organic market participation.
- **Asynchronous Scheduler:** Implemented via `asyncio` queues that dynamically calculate execution timestamps based on event metadata.

### B. Client Environment & Session Management

- **Automated Web Driver Adapter (`qbet.adapters.browser`):** Uses standard Playwright browser automation with consistent rendering configurations to maintain execution parity across web interfaces. (Playwright Stealth to emulate standard browser)
- **Network Interface & Gateway Isolation:** Maps each integration account to a dedicated static network gateway to ensure connection stability and consistent routing protocols.
- **Stateful Session Management:** Persists local storage, cookies, and authentication context metadata per adapter instance to eliminate redundant login requests and minimize network overhead.

### C. Dynamic Stake Rounding & Positive EV Constraints

- **Stake Optimization (`qbet.calculations.rounding`):** Exact mathematical lay stakes (e.g., €14.63) are deterministically rounded to standard integer or 5-unit increments (e.g., €15.00).

- **Expected Value Gatekeeper:** Stake adjustments are accepted if and only if the post-rounding outcome maintains a positive expected value ($EV > 0$).

### D. Baseline Activity Emulation (Traffic Diversification)

- **Baseline Activity Model (`qbet.layers.verification.baseline`):** Emulates regular retail activity by placing controlled, non-promotional allocations on highly liquid mainstream events (e.g., UEFA Champions League, English Premier League).

### E. Stake Exposure Capping & Limits

- **Exposure Guardrail (`qbet.layers.verification.exposure`):** Automatically caps individual order amounts to a maximum of 60%–70% of the provider’s reported max-bet limit, preventing max-stake execution flags.

### F. Low-Liquidity Market Exclusion

- **Liquidity Filter (`qbet.layers.verification.filters`):** Blocks order generation on sub-tier, low-volume markets to avoid slippage and manual audit flags.

### G. Temporal Execution Pacing

- **Staggered Order Routing (`qbet.layers.verification.pacing`):** Injects non-deterministic execution delays using `asyncio.sleep(random.uniform(4, 18))` prior to order submission.

---

## 3. Technology Stack & Persistence Architecture

- **Runtime & Domain:** Python 3.12+ with Pydantic v2 models for strict type enforcement and request validation.
- **Layer 1 (Math Layer):** Pure, stateless in-memory calculation modules using NumPy and Python `Decimal` (zero database dependencies).
- **Layer 2 (Verification Layer):** In-Memory Cache synchronized with SQLite (local development) and Supabase / PostgreSQL (production target).
- **Layer 3 (Capital Ledger Layer):** ACID-compliant ledger tracking wallet balances, exposure, and reserved liability[cite: 6].
- **Layer 4 (Execution & GUI Layer):** SQLite/Supabase persistent store with filesystem output for structured CSV/JSON audit reports[cite: 6].

---

## 4. Code Isolation & Tax Modeling Standards

### A. Protocol-Based Strategy Decoupling

Monolithic input unions (such as a unified `CalculationInput` in `base.py`) are strictly prohibited. Engines must inherit from the generic strategy protocol:

```python
from typing import Protocol, TypeVar

RequestT = TypeVar("RequestT", contravariant=True)
EvaluationT = TypeVar("EvaluationT", covariant=True)

class StrategyEngine(Protocol[RequestT, EvaluationT]):
    def evaluate(self, request: RequestT) -> EvaluationT:
```

### B. German Betting Tax Modeling (`TaxMode`)

All calculations must explicitly parameterize the applicable taxation rules:

- `STAKE`: Tax (5.3%) deducted directly from the initial back stake ($B_{eff} = B \cdot (1 - 0.053)$).
- `PROFIT`: Tax (5.3%) applied exclusively to net profit.
- `NONE`: Tax-free execution.

## Bank API Feasibility

Desired first candidates are Revolut or ING. Commerzbank and other banks can be considered.

Before choosing, create a research ticket that checks:

- whether an official API exists for personal or business accounts
- whether access is available in the user's country/account type
- authentication model
- sandbox availability
- transaction/balance access
- payment initiation/top-up support
- rate limits and costs
- compliance and data-retention obligations

Expected output: a short recommendation and connector decision. Until then, the bank layer should be designed behind an interface and backed by mock data.

## Current Implementation Alignment

The code currently has significant v1 foundations: typed domain models, pure calculators, deterministic rounding, a Base Engine dispatcher, simulation contracts, a Django web shell, and a proposal-only capital orchestrator. The desired target is to evolve the current Base Engine split into explicit `BonusEngine` and `SportsCapitalEngine` boundaries while preserving shared calculation primitives.

Provider-state persistence, simulation history, data collectors, execution adapters, bank connector, Supabase integration, and richer GUI interaction remain future implementation work.

## Suggested Next Milestones

1. Align code structure with `BonusEngine` and `SportsCapitalEngine` as separate v1 engines while keeping calculators shared.
2. Strengthen Layer 2 operational risk models: `ProviderState`, `AccountState`, `VerificationResult`, cooldowns, exposure thresholds, market filters, and warning statuses.
3. Connect simulation flows to the v1 engines and orchestrator, not only mocked generic steps.
4. Add persistence for provider state, simulation runs, execution plans, account actions, and reports.
5. Add GUI order approval/rejection, engine detail views, simulation controls, and report/export actions.
6. Add data collection and adapter contracts for Playwright collectors and API-backed engines.
7. Research and implement the selected bank connector behind a strict approval interface.
8. Add CI/CD and deployment readiness for a cloud-hosted web app.

## Definition Of Done

A ticket is done when:

- its acceptance criteria pass
- tests relevant to changed behavior exist and pass
- README Current Status is updated when project progress changed
- architecture docs are updated when contracts or direction changed
- the change is small enough to review without archaeology
- money movement and execution boundaries are explicit when bank or execution code is touched
- new engine behavior remains assigned to the correct layer and engine boundary

## Product Bias

Build the working v1 sports-betting product first: `BonusEngine`, `SportsCapitalEngine`, operational risk checks, capital orchestration, simulation, controlled execution, reports, and GUI approvals. Keep later engines connected through contracts and sandbox adapters so the system can grow without being rebuilt from scratch.
