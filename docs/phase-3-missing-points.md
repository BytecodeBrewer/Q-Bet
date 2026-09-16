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
| Market data | The Odds API read-only adapter is connected | use real adapter output in complete engine flows and targeted RequestHandler refreshes |
| RequestHandler | typed contracts, mode routing and sandbox handlers exist | live targeted adapter-backed revalidation/result retrieval |
| Smart Polling | deterministic provider-neutral policy exists | persistent/configurable provider-, quota-, cost-, timing- and engine-aware strategies |
| Bank/account | bunq read-only and official sandbox modes are implemented, including guarded sandbox payments | close the Simulation/Liquidity capital loop through the sandbox and back into Portfolio Ledger |
| Notifications | approval-gated notification domain and Django email transport exist | user preferences, internal inbox/SMS later, and composition with broader approval flows |
| Results/settlement data | provider-neutral result concepts exist | select/connect the first real result adapter and follow only actually executed work |
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

### 1. Real Data Flow Into Engine Pipelines

**Problem:** external adapters and the GUI/internal Simulation path exist, but adapter output is not yet the universal source of a complete connected Simulation run.

**Expected behavior:** the applicable engine receives normalized real adapter data, passes through only the preparation stages it actually needs, and then continues through risk/liquidity and the selected mode.

**Definition of Done:**

- a real adapter can feed the selected engine without hand-built GUI opportunity fixtures;
- sports data passes through the necessary match/market preparation path;
- engines without such preparation are not forced through a fake equivalent stage;
- correlation/identity survives through the flow;
- failures are mapped to safe application states rather than raw provider exceptions.

### 2. RequestHandler -> Targeted Adapter Revalidation

**Problem:** RequestHandler routing/contracts exist, but it does not yet provide the complete live targeted refresh behavior required by Phase 3.

**Expected behavior:** RequestHandler can call a configured adapter for the exact event/market/provider state that needs refreshing, without replaying broad ingestion.

It must support provider-aware behavior such as freshness, rate limits, API cost/quota, engine/mode context and optional cross-checking.

**Definition of Done:**

- a real adapter-backed RequestHandler implementation exists;
- event-specific revalidation is used close to action time;
- a rejected/changed/unavailable refresh produces the correct workflow decision;
- broad ingestion is not re-run solely for a targeted check;
- provider errors and quota/rate-limit states are mapped to stable reason codes;
- Simulation and Execution retain separate handler instances/state where required.

### 3. Close the Liquidity/Capital Simulation Loop

**Problem:** internal Simulation capital state and the bunq sandbox adapter exist, but the intended closed capital loop is not yet the default connected Simulation path.

**Expected behavior:** the Simulation sends the same or structurally equivalent financial requests used by the real execution path, but against sandbox/test systems. Simulated capital movement must be reflected back into Portfolio Ledger state.

**Definition of Done:**

- starting capital is represented in the connected Simulation financial path;
- LiquidityChecker decisions precede external sandbox movement;
- an approved/allowed Simulation action reaches the external sandbox where supported;
- provider success/failure is persisted and correlated;
- Portfolio Ledger receives the resulting state transition exactly once;
- replay/retry remains idempotent;
- no real-money write is possible from Simulation configuration.

### 4. Result/Settlement Source

Select/connect the first real result source from the existing candidate set when the complete flow needs final result state.

Only actions that were actually executed/simulated into a trackable external state need result polling. Rejected or ignored opportunities should be closed without unnecessary result requests.

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

### Configurable Smart Polling

The existing deterministic Smart Polling policy is a foundation, not the final scheduler.

Polling strategy must be configurable rather than hard-coded. Configuration should be capable of expressing:

- multiple refresh points such as T-24h, T-12h, T-1h and other intervals;
- provider-specific rate limits/quotas;
- free vs paid subscription capacity/cost;
- required freshness;
- engine-specific behavior;
- increasing/decreasing polling aggressiveness;
- final targeted revalidation separately from ordinary aggregation;
- result polling only for work that needs settlement tracking.

The pipeline should execute the configured strategy reliably; it should not contain provider business assumptions scattered throughout workflow code.

### Admin-Editable Polling

Phase 3 should persist polling/provider strategy so an admin can later change cadence and source behavior without a code deployment. A higher-limit paid API plan should therefore be usable by editing configuration rather than modifying Python constants.

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

1. Connect RequestHandler to targeted real adapter revalidation.
2. Make Smart Polling configuration-driven and provider/quota/cost aware.
3. Compose the first closed connected Simulation pipeline, including the sandbox financial round-trip and Portfolio Ledger feedback.
4. Add genuine data-pipeline and capital-loop E2E tests for that connected flow.
5. Audit/refactor the broader test suite against the now-real architecture.
6. Connect the first real result/settlement provider as required by the closed lifecycle.
7. Add Prometheus-compatible metrics and dashboard integration.
8. Expand Admin Control Center, Settings, Approval Inbox, Reporting/Monitoring dashboards and visual UX.

The exact order between items 3, 4 and 6 may shift when a chosen E2E scenario requires final result data, but the closed-loop definition must not be weakened to make a test easier to label E2E.

## Documentation Hygiene

The README is a project overview, not a changelog.

- `Current Status` should remain short prose, not a growing list of implementation bullets.
- Small refactors should normally not change `Current Status` at all.
- Individual adapters/features should be mentioned only when they materially change the project state.
- Detailed implementation history belongs in Issues, Pull Requests and Releases.
- This document should carry the richer Phase 3 missing-point context so future agents do not need to reconstruct it from chat history.
