# Q-Bet Expectation Model

This document is the authoritative expectation model for Q-Bet code, tickets, agents, and review. It is the single working source for product direction, architecture, delivery phases, and agent decisions.

## Source Of Truth

Agents must follow this file first. [Pipeline Architecture](pipeline-architecture.md) provides four visual views of the same system and must use the canonical component names defined here. [Phase 3 Integration Register](phase-3-integration-register.md) is the companion source for concrete user-provided external integrations and must be consulted before opening broad provider-research work. If code, README text, older issues, previous architecture wording, or integration notes conflict with this model, create or prioritize the smallest refactor ticket needed to restore alignment.

## North Star

Q-Bet is a deterministic, mathematically protected multi-asset quant platform. It identifies and evaluates market inefficiencies, promotional opportunities, arbitrage situations, and yield mechanisms across sports betting, event tickets, crypto delta-neutral strategies, and prediction markets.

The system should eliminate directional risk where possible, or operate only under clearly positive expected value where risk remains. It exposes decisions through a full GUI, isolates mathematical logic from execution and persistence, and protects capital through explicit domain-risk and liquidity controls.

Core principles:

- **Zero-CLI / Full GUI First:** End users operate simulation, approvals, settings, logs, exports, and analysis through the GUI.
- **Typed autonomous engines:** Engines implement explicit request/result contracts and share only pure calculation primitives.
- **Domain and pipeline separation:** Pure calculations never read databases, accounts, sessions, balances, browser profiles, GUI state, or execution state.
- **Capital separation:** `WorkflowOrchestrator` never owns balances or moves capital. `LiquidityChecker` validates proposed allocations; `PortfolioLedger` owns balances, reservations, settlements, and capital-movement proposals.
- **Conformant data access:** Quotation and market ingestion uses permitted, structured REST/WebSocket APIs. Browser automation is not a quotation-scraping channel.
- **Controlled execution:** Money movement and irreversible live actions require explicit user approval.
- **User choice inside platform guardrails:** Staff controls define global engine/mode availability and may disable a route for safety or maintenance. Within globally available routes, the user chooses which engines and modes to use for their own account context; a staff availability switch is not a substitute for per-user intent or execution approval.
- **Observable workflows:** Every material transition emits structured, correlatable events.
- **Shared operational source of truth:** Normal local and hosted Q-Bet runtimes use Supabase/PostgreSQL for durable operational state. Runtime-local analytics storage never becomes authoritative application state.
- **Small implementation tickets:** Work remains independently testable and reviewable.

## Architecture Contract

Q-Bet is a workflow-orchestrated pipeline. Not every engine uses every stage; each takes the shortest valid path.

```text
Data Aggregation -> Engine-specific Preparation -> Calculation -> Domain Risk -> LiquidityChecker -> Simulation / Execution -> Settlement
```

The detailed architecture is intentionally split into four Mermaid views in `docs/pipeline-architecture.md`:

1. Data and Processing Flow
2. Capital and Liquidity Flow
3. Monitoring and Tracking Flow
4. System Context

### Canonical Components

- `WorkflowOrchestrator`: pipeline routing, correlation ids, stage transitions, engine activation, throttling, and GUI-originated control.
- `RequestHandler`: optional side channel for targeted risk, balance, provider, or execution refreshes. It is not the main intake stream.
- `Data Aggregation`: normalized batch/stream intake from conformant structured APIs.
- `Sports Match Builder`: validated event, market, bookmaker BACK, exchange LAY, and Dutching coverage preparation.
- Calculation engines: deterministic strategy evaluation with typed inputs and outputs.
- `Domain Risk`: engine/domain-specific policy after calculation and before liquidity allocation.
- `LiquidityChecker`: validates whether an engine proposal may use the required capital, based on availability, allocation priority, exposure, account/provider state, and policy.
- `PortfolioLedger`: independent capital-domain authority for available, working, reserved, locked, pending, cost, settlement, and capital-movement state.
- Simulation and Execution: sibling targets with separate queues, adapters, histories, and capital contexts.
- Monitoring and Tracking Plane: structured events, logging, persistence, reports, exports, GUI visibility, and approvals.

### Engine Paths

- `BonusEngine` and `SportsCapitalEngine`: `Data Aggregation` -> `Sports Match Builder` -> calculation -> `Domain Risk` -> `LiquidityChecker`.
- `TicketEngine`: `Data Aggregation` -> `Ticket Preparation` -> `TicketEngine` -> `Domain Risk` where required -> `LiquidityChecker`.
- `PredictionMarketEngine`: `Data Aggregation` -> optional `Feature / Signal Builder` -> `PredictionMarketEngine` -> optional `Domain Risk` -> `LiquidityChecker`.
- `CryptoYieldEngine`: streaming `Data Aggregation` -> `Market State Aggregator` -> `CryptoYieldEngine` -> optional `Domain Risk` -> `LiquidityChecker`.

### Hard Separation Rules

- `WorkflowOrchestrator` does not own capital, balances, reservations, or ledger mutations.
- `LiquidityChecker` is a pipeline stage, not a child of `WorkflowOrchestrator`.
- `PortfolioLedger` is the capital authority. It is not an internal subcomponent of `LiquidityChecker` and does not belong to `WorkflowOrchestrator`.
- Bank, exchange, bookmaker, market, notification, and browser integrations belong under adapters.
- Execution emits results; `Capital Settlement` applies those results to the ledger and persistence.
- The GUI observes/configures through `WorkflowOrchestrator` and never directly mutates calculation, risk, liquidity, or execution state.
- Simulation and live execution must never share balances, queues, or result histories.
- Global staff availability and per-user engine selection are separate concerns. A global disable prevents new work from entering that route, but must not silently delete, unwind, or corrupt already-dispatched or unsettled state. Full drain/freeze/recovery semantics for live work are a Phase 4 responsibility.

## API-First Data Ingestion And Settlement

- Market and quotation ingestion uses structured, permitted REST/WebSocket APIs.
- Scraping bookmaker pages to harvest quotations is out of scope.
- Initial odds candidates are `The Odds API` and `Odds-API.io`, subject to a connector research ticket checking coverage, terms, limits, and costs.
- Initial free or low-cost result-data candidates are `football-data.org` and `OpenLigaDB`. They are settlement candidates, not authoritative assumptions for every sport or league.
- Exchange and market adapters should prefer official APIs such as the Betfair Exchange Betting/Stream/Accounts APIs, Polymarket CLOB/market-data APIs, and later suitable crypto exchange REST/WebSocket APIs.
- Smart Polling performs targeted, provider-efficient requests rather than periodic full-data crawling. A target policy may include baseline discovery, a T-24h candidate check, a T-2h liquidity check, and a final T-15m execution check. Requests should group relevant markets when the provider supports multi-market endpoints and respect provider rate limits, caching rules, and terms.
- Match settlement uses separate result-data APIs where practical so quotation API budgets are not consumed by settlement.
- Adapters normalize provider-specific payloads before domain preparation.
- Mock providers remain mandatory for integration tests and sandbox runs.
- Concrete Phase 3 provider choices supplied by the user are recorded in `docs/phase-3-integration-register.md` and take precedence over generic discovery candidates. Agents should not repeat provider research unless explicitly requested or the register marks an entry as requiring research.

### Pre-Execution Revalidation

Only live Execution triggers mandatory last-mile provider refresh. Before an approved order is submitted, `RequestHandler` revalidates current odds, market availability, balance, provider/account state, exposure, and timing. If data changed, the plan is recalculated and returned through `Domain Risk` and `LiquidityChecker`; if it is no longer valid, it is discarded.

Simulation does not perform live refreshes or send user notifications. It nevertheless uses the same typed invalidation outcomes: an invalid, rejected, expired, or underfunded plan is discarded rather than force-completed. Both paths therefore share decision semantics without sharing live side effects.

## Engine And Strategy Portfolio

| Category | Engine | Primary mechanism | Product horizon |
| --- | --- | --- | --- |
| Promotional sports | `BonusEngine` | Qualifying bets, SNR/SR free bets, reloads, cashback, promo conversion | Current product |
| Sports capital | `SportsCapitalEngine` | Arbitrage, odds boosts, real-capital matched betting, dutching | Current product |
| Tickets | `TicketEngine` | Event-driven secondary ticket opportunities | Later engine |
| Prediction markets | `PredictionMarketEngine` | Market making, order-book arbitrage, supported API strategies | Later engine |
| Crypto yield | `CryptoYieldEngine` | Delta-neutral spot/perpetual and funding-rate strategies | Later engine |
| Statistical edge | `MLEdgeLayer` | Fair odds, value detection, and drift checks | Later support layer |

Yield and profit figures are simulation hypotheses, never promises. Capital bands may be used in experiments and reports, but they do not define the delivery roadmap.

## Formula And Calculation Standards

All money-sensitive calculations use `Decimal` and deterministic rounding. NumPy may be used for vectorized analysis or simulation only when precision boundaries are explicit.

- Qualifying Bet Lay Stake: `L = (B * O_b) / (O_l - c)`.
- SNR Free Bet Lay Stake: `L_SNR = (B * (O_b - 1)) / (O_l - c)`.
- Value Bet EV: compare offered odds with margin-adjusted fair probability, then use fractional Kelly sizing when enabled.

Where `B` is bookmaker back stake, `O_b` is bookmaker back odds, `O_l` is exchange lay odds, and `c` is exchange commission.

Calculations explicitly model stake precision, rounding, fees, taxes, commission, liquidity, stake limits, total-stake limits, liability, and capital lock-up.

Implementation standards:

- No monolithic catch-all request/input union may own strategy-specific inputs.
- `BonusEngine` and `SportsCapitalEngine` remain autonomous `StrategyEngine[RequestT, EvaluationT]` implementations.
- Shared value objects and pure calculators may be reused; engine requests, evaluations, reports, and risk categories remain separate.
- Every `calculate_*` function requires at least a happy-path test and a material edge-case test.
- Dynamic rounding tests cover zero/near-zero candidates, configured increments, liability limits, liquidity limits, and total-stake limits.

German market tax modes:

- `STAKE`: apply 5.3 percent to stake-based taxable turnover.
- `PROFIT`: apply 5.3 percent to taxable winnings/profit.
- `NONE`: no betting-tax deduction.
- Tax mode is explicit in inputs, reports, and tests whenever final P/L can change.

## Matched-Betting Requirements

The current sports product contains two engines:

- `BonusEngine`: qualifying bets, SNR/SR free bets, reload/cashback offers, bonus-condition tracking, and promo conversion reports.
- `SportsCapitalEngine`: two-way arbitrage, multi-outcome dutching, odds boosts, real-capital matched-betting opportunities, and later value-betting candidates.

The stack includes qualifying-bet calculation, SNR/SR free-bet calculation, arbitrage detection, two-to-four-outcome dutching, dynamic rounding, stake optimization, tax/fee/commission handling, liquidity and stake limits, liability, execution-plan generation, result reporting, and account-operation warnings.

Dynamic rounding evaluates permitted floor/ceiling candidates at configured increments against final outcome values. The selected plan maximizes the least favorable eligible outcome while respecting liability, liquidity, total-stake limits, and strategy tolerance. A rounded plan is valid only when it remains mathematically sound.

## Domain Risk And Controlled Execution

Domain risk never alters pure math. It evaluates a completed strategy result and emits typed allow, warn, approval-required, reject, or recheck outcomes.

Required policy categories where relevant:

- kickoff proximity and market freshness
- provider and account state
- active-bet and concurrent-execution limits
- stake and total exposure limits
- low-liquidity and niche-market filters
- deterministic operational cooldowns and queue pacing
- withdrawal warnings and capital-cycle state
- session safety and credential isolation metadata
- slippage, funding, and margin risk for crypto paths

Execution policy must be transparent, configurable, deterministic where test reproducibility matters, and documented as stability/capital protection. It must not attempt to conceal automation, imitate human behavior, or bypass provider controls.

Browser automation rules:

- The first product approach is API-first and notification-first, without Playwright execution.
- `qbet.adapters.browser` may later support explicitly permitted, short-lived execution tasks for softbookers or ticket providers where no supported order API exists.
- Stored session contexts may reduce redundant authentication while remaining encrypted, scoped, and revocable.
- Browser automation is excluded from quotation scraping.
- Browser automation must use an ordinary user-controlled browser context. Cloudflare or provider verification is completed through the provider's intended browser flow; Q-Bet must not bypass, defeat, or imitate those controls.
- Queue pacing prevents race conditions, duplicate submissions, and local resource overload.
- Live orders require current risk/liquidity checks and explicit approval.

### Operational-Risk Blueprint Preserved From The Previous Model

The following blueprint is preserved verbatim from the previous expectation model. It records the original product requirements. The compliance rule above remains authoritative during implementation: these items may protect capital, stability, and sessions, but must not be implemented to conceal automation or bypass provider controls.

### A. Pre-Match Liquidity & Kickoff Proximity Scheduling

- **Event Timing Window:** Orders must be evaluated and scheduled close to event kickoff (typically $T-15$ to $T-5$ minutes). This maximizes market volume, minimizes odds drift, and aligns execution with organic market participation.
- **Asynchronous Scheduler:** Implemented via `asyncio` queues that dynamically calculate execution timestamps based on event metadata.

### B. Client Environment & Session Management

- **Automated Web Driver Adapter (`qbet.adapters.browser`):** Uses standard Playwright browser automation with consistent rendering configurations to maintain execution parity across web interfaces.
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

## Liquidity, Portfolio Ledger, And Financial Interfaces

`LiquidityChecker` should be reusable and stateless at the service boundary: the check call receives the opportunity snapshot, policy/configuration, and read-only capital/provider state required for that decision. It approves, rejects, or requests re-evaluation of an engine proposal; it does not execute financial flows.

Allocation ranks candidates by expected value, ROI, risk, liquidity, and capital lock-up, followed by a stable business key such as `opportunity_id` or `created_at`. Random UUID ordering must not be the reproducibility tie-breaker.

`PortfolioLedger` owns financial state transitions and distinguishes:

- available central capital
- working capital held on bookmaker, exchange, ticket, prediction-market, or crypto accounts
- reserved capital
- locked capital in active operations
- pending transfers or settlement
- realized P/L
- taxes, fees, commission, and infrastructure costs

Bank and provider adapters expose narrow typed protocols for balances, transactions, and prepared transfers. `PortfolioLedger` may calculate and propose capital movement; it must not know provider-specific payloads. `LiquidityChecker` reads the resulting state only for allocation decisions.

When capital should move, `PortfolioLedger` creates a `CapitalMovementProposal` containing source, destination, reason, requested amount, minimum useful amount, percentage of source balance, resulting reserve, and risk level. `NotificationService` sends the proposal to the user. The user approves an amount inside the permitted range, rejects it, or completes a manual provider withdrawal. Simulation never sends these notifications.

Email is the default low-cost Phase 3 notification channel. SMS is an optional paid adapter; current German SMS delivery through services such as Twilio is usage-priced rather than genuinely free. Later channels may include push notifications or another explicitly approved messaging adapter. Phase 3 uses notifications to bring the user back to Q-Bet or the relevant manual provider action instead of treating browser automation as the default execution mechanism.

The bank/payment implementation proceeds in increasing authority:

1. read-only balances and transactions
2. sandbox transfer simulation
3. payment draft or prepared transfer requiring human approval
4. live payment initiation only through an officially supported interface and eligible account or regulated provider

Revolut Business is a concrete research candidate because its Business API documents accounts, transactions, payment drafts, transfers, sandbox simulations, and webhooks. ING PSD2 sandbox APIs are useful for feasibility tests, but production payment initiation generally requires an appropriately certified third party. A normal private bank account must not be assumed to provide unrestricted payment automation.

Before selecting a real bank connector, a research ticket must verify official API availability, country/account eligibility, authentication, sandbox support, balance/transaction access, payment initiation, rate limits, cost, compliance, and retention requirements. Until that decision, the bank layer uses mock/sandbox data. If the user supplies a concrete connector choice in `docs/phase-3-integration-register.md`, agents use that entry directly and research only the unresolved capability or compliance questions attached to it.

## Simulation And Execution

- Simulation and Execution are sibling dispatch targets.
- Simulation uses virtual capital, simulated adapters, selectable engines, visible progress, stop/end controls, and persisted reports.
- Placeholder adapters for later engines remain explicit until their real logic exists.
- Live execution is enabled only for supported workflows and requires approval boundaries.
- API execution is the preferred target for betting exchanges, prediction markets, and crypto markets. Betfair Exchange and Polymarket are concrete initial research candidates.
- Phase 3 is notification-first for provider flows that do not expose an approved direct execution API. The initial user-facing reaction is an email/GUI notification and manual action, not automatic browser navigation or order placement.
- Narrowly scoped Playwright execution is a later Phase 4 opt-in adapter for explicitly permitted flows, not a Phase 3 prerequisite.
- Result payloads are validated with typed models before report or settlement generation; invalid payloads fail visibly and do not partially update capital state.
- Execution and simulation results carry correlation ids and remain queryable in separate histories.

## Storage, Logging, And GUI

| Concern | Current target | Later target |
| --- | --- | --- |
| Pure calculation | Stateless in memory | Same |
| Operational state and ledger | Supabase/PostgreSQL as shared durable source of truth | Scale/HA and object placement as justified |
| Analytics, replay, backtesting | DuckDB per runtime plus export files | Durable remote/lakehouse placement as justified |
| Reports and exports | PostgreSQL plus CSV/JSON | Object storage as justified for larger artifacts |

Normal local and hosted Q-Bet runtimes require `QBET_DATABASE_URL` and use PostgreSQL for durable application state. Local and deployed web applications may intentionally reference the same Supabase database, so no SQLite/PostgreSQL synchronization layer exists. Automated tests must instead use an isolated disposable PostgreSQL target and must never mutate the shared production Supabase database.

DuckDB is runtime-local analytical infrastructure. A local process and a Vercel runtime have separate DuckDB contexts, and neither is authoritative. Any simulation, workflow, report, provider, user, session, monitoring, or future ledger state that must survive runtime termination or be visible across environments is persisted to PostgreSQL. SQLite is legacy-only and may appear only in explicit one-time import tooling or archived documentation.

Structured JSON logging is the machine-readable contract for pipeline diagnostics and agentic tickets. Material events include correlation id, stage, engine, status, reason/decision code, timestamp, and safe references to inputs/outputs. Secrets, credentials, session tokens, and raw sensitive payloads are never logged.

The Django GUI is the current Admin Control and Monitoring Plane plus the customer product surface. Django Admin remains the central user/permission administration surface for authorized administrators, while Q-Bet-specific admin views expose simulation controls, global engine/mode availability, and monitoring. Staff global controls act as platform availability/safety gates. They do not define which globally available engine a customer must use. Normal users should increasingly control their own engine selection and account-scoped preferences while seeing only their engine views, approvals, reports, and assigned capital/subaccount state. Compact engine widgets show traffic-light status and warning/error symbols. Drag-and-drop layout is optional, not a completion requirement.

The system must retain at least one active staff superuser. The final active staff superuser cannot be deleted, deactivated, or stripped of `is_staff`/`is_superuser`; this guard must be enforced server-side and covered by tests.

## Delivery Roadmap

Global MoSCoW prioritization is removed. Each delivery phase has explicit completion scope and explicit exclusions. Later phases may begin discovery work, but a phase is not complete until all its in-scope outcomes are verified.

### Phase 1 - Pipeline Foundation (Completed Baseline)

In scope:

- typed domain models and deterministic sports calculators
- separate `BonusEngine` and `SportsCapitalEngine`
- dynamic rounding and core operational-risk contracts
- sports data contracts and `Sports Match Builder`
- simulation contracts, workflow transitions, provider-state persistence, and report history
- legacy proposal allocation through `CapitalOrchestrator` as a bridge to `LiquidityChecker`

Out of scope for completion:

- polished GUI, live provider adapters, bank connectivity, production execution, cloud scaling

### Phase 2 - Monitoring And Operability (Current)

In scope:

- `WorkflowOrchestrator` boundary and correlation ids across the connected sports path
- deterministic mode-specific `RequestHandler` revalidation/result seams
- structured logging for every material stage and decision
- shared PostgreSQL persistence for pipeline, simulation, provider, report, routing, monitoring, queue, ledger, and controlled execution state
- authoritative `PortfolioLedger` transitions for reservation, lock, pending, settlement, cost/failure state, and restart recovery
- separate Simulation and deterministic/mock Execution queues, histories, capital contexts, result handling, and settlement
- customer-facing Reporting separated from administrator-only technical Monitoring, including bounded exports and safe warning/error presentation
- usable Django admin/control GUI with stage visibility, queues, warnings, logs, reports, exports, simulation controls, routing configuration, and user/permission administration
- staff-wide platform availability controls plus a bounded normal-user engine/report/subaccount view
- final composition fixes that ensure GUI-started Simulation uses durable ledger state and queued Execution waits at a real explicit approval boundary
- a final connected Phase 2 GUI-to-workflow E2E gate after those composition fixes are complete

Out of scope for completion:

- real external market/result adapters, bank connectivity, email notifications, production browser execution, broad multi-user isolation, Kubernetes/cloud orchestration, and later engine production logic

### Phase 3 - External Integrations, Notifications, And Operational Validation

Phase 3 is the controlled connection to the outside world. Internal Phase 2 boundaries remain intact while real data, account, notification, and observability adapters are attached behind them.

In scope:

- conformant external odds/market API adapters and separate result-data/settlement adapters using the concrete choices recorded in `docs/phase-3-integration-register.md`
- configurable smart polling and live `RequestHandler` refreshes around external provider state
- bank/account connectivity beginning with supported read-only, sandbox, transaction visibility, or approval-based capabilities rather than unrestricted money movement
- mock/sandbox bank and provider APIs alongside real read-only or explicitly approved external adapters
- `NotificationService` with email as the initial channel for opportunities, required approvals, warnings, and capital-movement proposals
- notification/manual-action-first execution for providers that do not expose an approved direct execution API; automatic browser execution remains deferred
- per-user engine selection/preferences inside staff-controlled global engine/mode availability, without yet requiring isolated per-user cloud runtimes
- integration of established pipeline metrics/monitoring tooling, beginning with Prometheus-compatible metrics and retaining Q-Bet's structured Monitoring plane as the application diagnostic source
- repeatable performance/load measurements for the connected pipeline and targeted performance tests for important data, routing, persistence, and reporting paths
- deliberate operator-driven test runs started through the GUI against permitted sandbox, read-only, or approved external integrations so the system is exercised as a product rather than only through automated tests
- continued incremental GUI/product refinement: clearer pages, richer engine/account states, restrained animations, interaction polish, responsive improvements, and a visually coherent customer-facing experience
- continued correctness, failure-recovery, integration, and E2E coverage as each external boundary is introduced

Out of scope for completion:

- automatic browser order placement as the default provider path
- unmanaged or unapproved money movement
- production-scale isolated multi-user pipelines
- Kubernetes or broader cloud orchestration beyond the current Vercel/Supabase production baseline
- graceful live-position drain/freeze/restart orchestration for a globally disabled engine

### Phase 4 - Cloud, Multi-User Isolation, And Automation Hardening

Phase 4 turns the validated connected product into a deliberately cloud-operated multi-user system and introduces higher-authority automation only after the Phase 3 external boundaries are understood.

In scope:

- production cloud architecture beyond the current Vercel/Supabase baseline where required, including containerized services, infrastructure as code, and Kubernetes/orchestration when justified by workload isolation or scaling
- treating local Docker/runtime environments as development and preview environments rather than authoritative production state
- isolated per-user pipeline/runtime contexts so one customer's engine activity, bankroll, credentials, queues, histories, and failures do not share an execution context with another customer
- per-user bankroll/account isolation, roles, permissions, credentials, secrets, quotas, and operational limits
- clear three-level control semantics: staff global availability, user account-scoped engine selection, and per-operation approval/automation policy
- graceful engine shutdown/drain semantics: a global stop prevents new work while already-dispatched or unsettled work remains recoverable and can safely continue, freeze, settle, or be explicitly cancelled according to state-specific policy
- restart/recovery guarantees ensuring a stopped and later restarted engine cannot duplicate execution, lose settlement state, orphan reservations, or corrupt capital
- higher-automation execution adapters through officially supported APIs and, only where explicitly permitted, narrowly scoped browser execution
- production-scale performance, reliability, security, high-availability, incident recovery, observability, alerting, and capacity work building on Phase 3 metrics
- cloud placement/scaling of DuckDB/lakehouse analytics workloads where justified without making runtime-local analytics authoritative operational state

Out of scope for completion:

- unmanaged autonomy, attempts to bypass provider controls, or unapproved money movement
- broad product/market expansion that compromises stability of the validated sports product

### Phase 5 - Product Scale And Expansion

In scope:

- mature public or managed multi-tenant service operation after Phase 4 isolation and recovery contracts are proven
- 24/7-capable operation with alerts, notifications, controlled automation policies, support/incident tooling, and capacity management
- expansion of supported regions, providers, exchanges, and markets behind the established adapter contracts
- incremental production rollout of `TicketEngine`, `PredictionMarketEngine`, `CryptoYieldEngine`, and later `MLEdgeLayer` capabilities after current-product stability
- larger analytical/replay workloads and deliberate lakehouse/object-storage placement where justified
- commercial/product controls needed for sustainable multi-user operation

Out of scope:

- unmanaged full autonomy or unapproved money movement

## Current Implementation Alignment

The current code provides the Phase 1 foundations plus a near-complete Phase 2 operational system: typed models, pure calculators, deterministic rounding, separate sports engines, typed sports preparation, workflow correlation/logging, deterministic mode-specific `RequestHandler` seams, PostgreSQL-backed provider/routing/queue/monitoring/report state, authoritative ledger and execution persistence, restart recovery, deterministic sandbox execution and settlement, customer Reporting, administrator Monitoring/exports, Supabase-backed auth/sessions, Django Admin user/permission administration, and runtime-local DuckDB replay infrastructure. SQLite is no longer a normal runtime technology and remains only behind an explicit legacy importer.

The immediate priority remains the final Phase 2 composition work documented in README: connect GUI-started Simulation to durable ledger persistence, replace programmatic Execution approval with an explicit approval boundary, and then run the final connected GUI-to-workflow Phase 2 E2E gate. Once that gate is accepted, Phase 3 begins by connecting the existing internal boundaries to real external data/account sources, email notifications, Prometheus-compatible operational metrics, performance validation, and operator-driven product runs. Concrete external choices belong in `docs/phase-3-integration-register.md` so agents do not repeat unnecessary discovery work.

## Agent And Ticket Rules

- Tickets reference the delivery phase and name the affected canonical components.
- Acceptance criteria derive from this model, not merely from current implementation behavior.
- Test tickets may inspect public contracts and required instantiation points, but should avoid copying internal implementation structure into expected outcomes.
- If implementation conflicts with a hard separation rule, prioritize a bounded refactor ticket before unrelated features.
- Before creating Phase 3 provider/data/bank/notification/monitoring research work, consult `docs/phase-3-integration-register.md`. Use concrete user-provided choices directly and research only unresolved questions or explicitly requested alternatives.
- Keep real credentials, account secrets, bank movement, and live execution outside tickets unless explicitly approved by the user. Never store credentials in documentation or issue bodies.

## Definition Of Done

A ticket is done when:

- acceptance criteria pass
- tests for changed behavior and material edge cases exist and pass
- logs and failure behavior are observable where pipeline behavior changed
- README Current Status is updated when project progress changed
- architecture docs are updated when contracts or direction changed
- simulation and live boundaries remain explicit
- money movement and execution approval boundaries remain explicit
- engine behavior remains assigned to the correct stage and domain
- the change remains reviewable without archaeology

## Product Bias

Complete the working sports product first: `BonusEngine`, `SportsCapitalEngine`, sports `Domain Risk`, workflow orchestration, `LiquidityChecker`, simulation, controlled execution, reports, and GUI approvals. Phase 3 connects that product to external data, bank/account, notification, metrics, and operator-validation boundaries. Phase 4 then isolates users and hardens cloud operation and higher-automation execution without weakening capital, approval, or recovery guarantees. Keep later engines connected through typed contracts and sandbox adapters so the system can grow without being rebuilt.