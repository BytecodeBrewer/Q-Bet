# Phase 3 Missing Points and Completion Plan

## Purpose

This document is the working completion plan for Phase 3. `docs/expectation-model.md` remains the authoritative product/architecture source and `docs/pipeline-architecture.md` remains the flow reference. This file records the concrete missing connections, operating expectations, priority order, and Definitions of Done needed to turn the Phase 2 foundation into a genuinely connected system.

Ticket, Developer, and Reviewer Agents should use this document when a Phase 3 task touches pipeline composition, external adapters, Simulation/Execution, testing, observability, or the later GUI/control-center work. A short README bullet or isolated issue description is not enough context for the larger requirements below.

## Phase 3 Definition

Phase 2 established the deterministic internal platform. Phase 3 connects that platform to the outside world and closes the real business loops.

The target flow is:

```text
external data
  -> adapter / Data Ingestion
  -> optional engine-specific Preparation
  -> engine calculation
  -> Domain Risk
  -> LiquidityChecker
  -> Approval where required
  -> Simulation or Execution
  -> external sandbox / permitted external system
  -> result / settlement
  -> Portfolio Ledger
  -> Notification / Reporting / Monitoring / GUI
```

The central success criterion is not the number of available components. The components must execute together through the relevant complete loop.

## Current Baseline

| Area | Current state | Main Phase 3 gap |
| --- | --- | --- |
| Internal workflow | The first connected SportsCapital Phase 3 E2E gate composes real adapter boundaries, calculation/risk/liquidity, isolated modes, sandbox capital feedback, post-event settlement, persistence, Monitoring and Reporting; regression ownership is documented in `docs/test-strategy.md` | no remaining core Phase 3 test-ownership gap; extend focused coverage only when new connected behavior is added |
| Market data | The Odds API feeds GUI-started SportsCapital Simulation through fixed-odds sportsbook snapshots and the Sports Match Builder; German sportsbook identity is gated by the versioned GGL catalog before Bonus preparation | BonusEngine still needs provider-specific promotion/account metadata and later connected fixed-odds sportsbook market-data composition |
| RequestHandler | targeted pre-execution revalidation is adapter-backed through The Odds API and survives the durable dispatch path | no remaining core Phase 3 gap; post-event result collection is a separate settlement boundary |
| Smart Polling | persisted strategies now drive restart-safe PostgreSQL work through a protected bounded hosted tick; the connected SportsCapital Simulation route dispatches due The Odds API market work and records Monitoring outcomes | multi-provider source selection can be added when another source is introduced |
| Bank/account | bunq read-only and official sandbox modes are implemented, including durable idempotent Simulation funding feedback composed from completed Simulation work into Portfolio Ledger | no remaining core Phase 3 gap; broaden only when another bank/account flow requires it |
| Notifications | approval-gated notification domain, email-first delivery, durable per-user preferences, and a customer-safe internal inbox exist | SMS and broader approval-flow categories remain later work |
| Results/settlement data | The Odds API scores are connected through the provider-neutral result boundary and the connected E2E path to post-event SettlementService composition | no remaining core Phase 3 gap; add another result source only when coverage requires it |
| Monitoring/Reporting | technical Monitoring now includes bounded operational windows, summaries, durable Prometheus-compatible metrics, an importable Grafana dashboard, degraded-source visibility and configured infrastructure links; customer Reporting remains separate | customer/business Reporting UX improvements only |
| Performance | repeatable Phase 3 connected baseline exists | later provider/network hot-path baselines where useful |
| GUI controls | routing/runtime controls, persistent per-user engine/mode selection inside staff guardrails, and basic profile/admin surfaces exist | later consolidated Settings/Admin Control Center and product-quality UX |

## Architecture Rules

### Data Ingestion and Preparation

Data Ingestion retrieves and normalizes external data. Preparation is optional and engine-specific; the architecture must not invent a Match Builder or extra stage where the engine can already consume the normalized data directly.

- BonusEngine and SportsCapitalEngine use fixed-odds sportsbook match/market preparation.
- BonusEngine additionally requires explicit promotion/account metadata, which is separate from quotation data. German sportsbook identities must resolve through the GGL-backed canonical provider catalog before normalized offers can enter the German Bonus preparation path.
- Betting exchanges belong to the separate planned SportsExchangeEngine and can require exchange market/order-book preparation.
- Ticket arbitrage is expected to need less or no comparable Match Builder.
- Crypto is expected to consume normalized exchange/market data without a sports-style Match Builder.
- Engines receive usable typed inputs and own their calculation/transformation logic.

One module should have one clear responsibility. Do not decompose a simple transformation into many artificial components solely to make every engine look structurally identical.

### Notification and Approval Are Different Responsibilities

A Notification communicates that something happened or requires attention. An Approval is a business decision/state transition that can authorize a later action.

An Approval may trigger notifications through one or more channels, but it must remain a separately modeled workflow. Future official exchange/execution APIs should support: opportunity -> approval -> user confirmation -> automated permitted action.

### Portfolio Ledger Represents Capital; It Does Not Hold Capital

`PortfolioLedger` records balances, allocations, reservations, transactions and results. Capital itself lives in the connected accounts/platforms.

Therefore a realistic Simulation cannot terminate immediately before the external account boundary. Where a sandbox/test endpoint exists, Simulation should exercise a structurally equivalent financial path:

```text
Portfolio Ledger
  -> Liquidity / funding / execution logic
  -> external sandbox
  -> provider result
  -> Portfolio Ledger update
```

The same principle applies to external execution sandboxes: fake money is still expected to move inside the sandbox so the real integration boundary is tested without real capital.

## Priority 1 — Complete Technical Pipeline Composition

### Completed Composition Foundations

The first external-data composition slices are implemented:

- #146 connects controlled Execution RequestHandler revalidation to a targeted The Odds API refresh through the durable dispatch path.
- #147 persists provider/target/engine Smart Polling strategies with quota/cost metadata and staff configuration.
- #150 connects GUI-started SportsCapital Simulation to normalized The Odds API data through deterministic two-outcome selection and the existing Sports Match Builder.

These capabilities are no longer Phase 3 missing points. The external capital/result foundations are recorded separately below.

### Completed External Capital and Result Boundaries

The two remaining external-loop foundations are now implemented:

- #151 closes bunq fake-money sandbox funding feedback into the durable Simulation Portfolio Ledger with restart-safe idempotency and Simulation/Execution isolation.
- #154 connects exact-event The Odds API score/finality reads to the provider-neutral post-event result boundary and existing SettlementService without treating sports scores as financial execution outcomes.

With the earlier #146, #147 and #150 composition work, the major external boundaries needed for the first realistic connected flow now exist independently. The next Priority 1 objective is to prove them together through a genuine connected E2E gate rather than adding another isolated adapter.

## Priority 2 — Realistic Sandbox Integration

Sandbox/testing must model future production behavior, not only prove that an isolated adapter method works.

The first major connected Simulation should therefore exercise as much of the future path as provider sandboxes allow: real market data, relevant preparation, engine calculation, risk/liquidity, targeted revalidation, approval when applicable, external sandbox action, settlement/result handling, Portfolio Ledger update, notification/reporting/monitoring.

Sandbox and personal/production account modes must remain explicit configuration boundaries. Credentials stay in environment/deployment secrets and never in source, logs, issues, reports, fixtures, or documentation.

## Priority 3 — Genuine End-to-End Testing and Test-Suite Consolidation

### Connected E2E Gate

#155 adds the first genuine connected SportsCapital Phase 3 gate. Normal CI composes the production application/domain/repository boundaries while replacing only external network and sandbox transport edges with deterministic fakes. The gate proves mode isolation, exactly-once bunq Simulation funding feedback, post-event result settlement, durable PostgreSQL restoration, and Monitoring/Reporting visibility. External bunq sandbox writes remain separately opt-in.

### End-to-End Definition

A test is E2E only when it executes a complete relevant business flow. Several integration tests that individually cover adjacent modules do not become E2E merely because every module has some test coverage.

**Data-pipeline E2E example:**

```text
adapter -> ingestion -> required preparation -> engine -> risk/liquidity -> mode -> notification/approval/output
```

**Liquidity/capital E2E example:**

```text
bank/sandbox adapter -> Portfolio/Liquidity state -> LiquidityChecker
-> Simulation/Execution -> provider result -> Portfolio Ledger
```

The loop is complete only when the post-action capital/result state returns to the ledger.

Regression ownership, legacy-gate consolidation, and the durable test-layer strategy are now documented in `docs/test-strategy.md`. The connected Phase 3 SportsCapital gate remains the canonical composition regression while focused tests retain recovery, idempotency, authorization, calculation, and fail-closed responsibilities.

## Priority 5 — GUI and Operational Control

Large UX work belongs to the later part of Phase 3, after pipeline composition, sandbox realism, E2E coverage and operational stability.

### Unified Settings and Admin Control Center

Normal users and admins should share one application/settings structure. Admins receive additional authorized sections rather than a completely separate-looking product.

Normal-user settings should cover, as appropriate:

- profile/account data;
- password/security;
- phone/email verification;
- notification preferences;
- language, region, timezone/time format and display currency;
- appearance controls such as light/dark mode.

Admin/staff additions should include:

- engine configuration/control;
- adapter/API configuration;
- polling strategies;
- notifications/approvals;
- monitoring/reports;
- external service/system information;
- central business/bank account configuration where applicable;
- an entry point to Django Admin for low-level administration rather than exposing Django Admin as the primary product UI.

### Notification Preferences and Approval Inbox

Users should eventually choose channels per notification type. Initial channels are internal inbox, email and later SMS.

Approvals should also appear inside the application with an unread/open count, expiration handling and a visible remaining validity period. Expired approvals should disappear from the active queue automatically rather than requiring manual cleanup.

### Reporting UX

Customer/business Reporting remains separate from technical Monitoring.

Technical Monitoring time windows, operational summaries, infrastructure links, durable Prometheus-compatible metrics and the Grafana dashboard contract are implemented. Business-facing Reporting now provides bounded time windows, engine/mode filters, and business-only financial summaries without exposing internal implementation details.

### Navigation and Visual Design

Later Phase 3 UX work includes a collapsible sidebar/settings navigation, stronger interaction states/animations, clearer buttons, a less internally technical home page, and suitable functional illustrations/assets.

The home page should prioritize understandable current system state, running activity, opportunities, performance/development and portfolio/system movement rather than counts of internal modules.

A simple footer can provide contact/social placeholders. Do not expose a GitHub repository link because the repository is private.

### Localization

Support language, region, timezone/time format and currency as separate preferences. Region may provide defaults but must not permanently bind currency or display format. Public/unauthenticated surfaces can later infer reasonable defaults while still allowing override.

## Phase 3 Sportsbook Browser Seam

Where a sportsbook exposes no supported API for promotion/account metadata, Phase 3 may add a provider-specific read-only browser adapter for the user's own configured account. It may read promotion/account terms required for BonusEngine preparation, but it must not scrape quotations or place bets. Sessions remain isolated by provider integration and must use intended provider authentication/verification flows.

## Phase 4 Boundary

Phase 3 assumes a primary operator/user and central pipeline. The following remain Phase 4 architecture work unless a Phase 3 prerequisite explicitly requires a small seam now:

- multi-user pipeline/account/capital isolation;
- broader live-capital execution;
- higher-authority sportsbook browser execution where permitted;
- SportsExchangeEngine live execution and exchange-specific order lifecycle;
- broader production cloud orchestration and high-availability concerns;
- detailed Betting Exchange / later-engine execution architecture;
- graceful live drain/shutdown of already-dispatched work.

Phase 4 is expected to follow the same pattern: establish a stable transition architecture first, stabilize it, then expand authority and scale.

## Recommended Ticket Sequence

1. Expand the remaining Admin Control Center and Settings surfaces, notification preferences, customer Reporting, localization and product-quality visual UX.

The connected E2E definition remains the regression gate for future Phase 3 work: a complete relevant business loop with external boundaries represented by permitted read-only/sandbox interfaces and authoritative state returning to Q-Bet.

## Documentation Hygiene

The README is a project overview, not a changelog.

- `Current Status` should remain short prose, not a growing list of implementation bullets.
- Small refactors should normally not change `Current Status` at all.
- Individual adapters/features should be mentioned only when they materially change the project state.
- Detailed implementation history belongs in Issues, Pull Requests and Releases.
- This document should carry the richer Phase 3 missing-point context so future agents do not need to reconstruct it from chat history.
