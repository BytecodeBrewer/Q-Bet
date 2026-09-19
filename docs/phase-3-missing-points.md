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
| Internal workflow | Calculation, risk, liquidity, Simulation/Execution boundaries, persistence, approvals, settlement, reporting and monitoring exist | compose them with the real external paths instead of deterministic local-only inputs |
| Market data | The Odds API feeds GUI-started SportsCapital Simulation through normalized snapshots and the Sports Match Builder | broaden connected data composition only when another engine/provider requires it |
| RequestHandler | targeted pre-execution revalidation is adapter-backed through The Odds API and survives the durable dispatch path | no remaining core Phase 3 gap; post-event result collection is a separate settlement boundary |
| Smart Polling | persisted provider/target/engine strategies, quota/cost metadata and staff configuration are implemented | multi-provider source selection can be added when another source is introduced |
| Bank/account | bunq read-only and official sandbox modes are implemented, including durable idempotent Simulation funding feedback into Portfolio Ledger | compose the accepted bank boundary into the genuine connected E2E gate |
| Notifications | approval-gated notification domain and Django email transport exist | user preferences, internal inbox/SMS later, and composition with broader approval flows |
| Results/settlement data | The Odds API scores are connected through the provider-neutral result boundary to post-event SettlementService composition | compose the accepted result boundary into the genuine connected E2E gate |
| Monitoring/Reporting | persisted technical Monitoring and customer Reporting exist | richer time-window dashboards plus external/infrastructure observability |
| Performance | repeatable Phase 3 connected baseline exists | later provider/network hot-path baselines where useful |
| GUI controls | routing/runtime controls and basic profile/admin surfaces exist | later consolidated Settings/Admin Control Center and product-quality UX |

## Architecture Rules

### Data Ingestion and Preparation

Data Ingestion retrieves and normalizes external data. Preparation is optional and engine-specific; the architecture must not invent a Match Builder or extra stage where the engine can already consume the normalized data directly.

- Sports betting requires match/market identity matching and additional preparation.
- Betting exchanges can require matching/order-book preparation.
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

### Test-Suite Refactor

The repository already has a large test surface. Test count is not a quality metric by itself.

After connected paths replace older component-only architecture, review the complete suite for:

- missing tests for critical real paths;
- redundant tests that protect the same contract repeatedly;
- tests that preserve obsolete architecture;
- excessively detailed low-value cases;
- unit tests that remain valuable because they isolate important calculations/contracts;
- genuine E2E coverage that should replace groups of weaker integration tests.

Do not mass-delete unit tests. Remove/replace tests because their architectural responsibility is obsolete or redundant, not because the suite is large.

## Priority 4 — Operative Infrastructure

Smart Polling configuration and its staff-editable persistence are implemented by #147. The remaining operative-infrastructure focus is external observability and later multi-provider source balancing when another provider is actually introduced.

### Observability

Keep domain/business Monitoring separate from infrastructure observability.

- Q-Bet Monitoring: engines, matches/opportunities, pipeline stages, approvals, errors/warnings, capital/workflow state.
- Prometheus-compatible metrics: technical counters/gauges/histograms and provider/runtime metrics.
- Grafana or equivalent: dashboards, time windows and technical visualization rather than rebuilding a monitoring product inside Q-Bet.
- Hosted platform links/health (for example Vercel/Supabase/Grafana) can later be surfaced from the Admin Control Center.

Prometheus-compatible instrumentation and the selected dashboard integration belong to Phase 3 after the central connected loops are stable.

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

### Reporting and Monitoring UX

Customer/business Reporting should remain separate from technical Monitoring.

Reporting should support useful time windows and business-focused dashboards without exposing internal performance/implementation details. Monitoring should support operational time windows and dashboards for engine/pipeline health, warnings/errors and links to infrastructure observability.

### Navigation and Visual Design

Later Phase 3 UX work includes a collapsible sidebar/settings navigation, stronger interaction states/animations, clearer buttons, a less internally technical home page, and suitable functional illustrations/assets.

The home page should prioritize understandable current system state, running activity, opportunities, performance/development and portfolio/system movement rather than counts of internal modules.

A simple footer can provide contact/social placeholders. Do not expose a GitHub repository link because the repository is private.

### Localization

Support language, region, timezone/time format and currency as separate preferences. Region may provide defaults but must not permanently bind currency or display format. Public/unauthenticated surfaces can later infer reasonable defaults while still allowing override.

## Phase 4 Boundary

Phase 3 assumes a primary operator/user and central pipeline. The following remain Phase 4 architecture work unless a Phase 3 prerequisite explicitly requires a small seam now:

- multi-user pipeline/account/capital isolation;
- broader live-capital execution;
- per-user browser sessions and provider identities;
- higher-automation browser execution where permitted;
- broader production cloud orchestration and high-availability concerns;
- detailed Betting Exchange / later-engine execution architecture;
- graceful live drain/shutdown of already-dispatched work.

Phase 4 is expected to follow the same pattern: establish a stable transition architecture first, stabilize it, then expand authority and scale.

## Recommended Ticket Sequence

1. Add a genuine connected E2E gate that composes the accepted real market-data path, risk/liquidity boundaries, sandbox capital feedback, post-event result collection, SettlementService and final Portfolio Ledger state.
2. Audit/refactor the broader test suite against the now-real architecture, replacing obsolete or redundant component-only protection where genuine E2E coverage owns the responsibility.
3. Add Prometheus-compatible metrics and dashboard integration around the stable connected flow.
4. Expand Admin Control Center, Settings, Approval Inbox, Reporting/Monitoring dashboards and visual UX.

Do not weaken the connected E2E definition merely to make the next test easier. The first gate should demonstrate a complete relevant business loop with external boundaries represented by their permitted read-only/sandbox interfaces and authoritative state returning to Q-Bet.

## Documentation Hygiene

The README is a project overview, not a changelog.

- `Current Status` should remain short prose, not a growing list of implementation bullets.
- Small refactors should normally not change `Current Status` at all.
- Individual adapters/features should be mentioned only when they materially change the project state.
- Detailed implementation history belongs in Issues, Pull Requests and Releases.
- This document should carry the richer Phase 3 missing-point context so future agents do not need to reconstruct it from chat history.
