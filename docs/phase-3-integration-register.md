# Phase 3 Integration Register

This file is the companion register for concrete Phase 3 external integrations. It stores user-approved or user-provided integration choices so ticket and development agents do not repeatedly research providers that have already been selected.

`docs/expectation-model.md` remains the authoritative architecture and roadmap document. This register supplies concrete Phase 3 integration inputs only; it does not override architecture contracts.

## Agent Rules

- Prefer an explicit user-provided integration entry over fresh provider discovery.
- Do not replace a named provider, API, bank, or monitoring tool with an alternative unless the user asks for comparison or research.
- Research is required only when the user explicitly requests it or an entry is marked `research needed`.
- Treat historical pricing, free-tier, transport, and coverage descriptions as unverified until a Phase 3 ticket checks the selected provider's current documentation. Do not re-open capabilities that the user has already explicitly verified unless implementation details require it.
- Record only capabilities, intended role, known constraints, and implementation status. Never store credentials, tokens, passwords, account numbers, session secrets, payment links, private keys, or other unnecessary personal account identifiers here.
- External integrations must use permitted interfaces and documented provider flows. Browser automation is not a quotation-scraping channel.
- Phase 3 is notification-first for execution paths that do not have an approved direct execution API. Higher-automation browser execution is deferred to Phase 4.
- Existing credentials must be consumed only through repository/deployment secrets or environment configuration; never copy secret values into source, issues, logs, reports, test fixtures, or documentation.

## Confirmed Phase 3 Direction

| Area | Phase 3 intent | Concrete choice | Status |
| --- | --- | --- | --- |
| Data Aggregation / odds | Feed normalized candidate markets and quotations into the sports pipeline through typed adapters. | The Odds API is the selected first read-only quotation adapter; Odds-API.io, OddsPapi, BetBurger, and OddsJam remain future candidates. | The Odds API adapter connected |
| RequestHandler revalidation | Perform targeted event-specific refreshes close to execution using current quotation/provider state rather than repeating broad ingestion. | Reuse selected quotation adapters with source-aware targeted pulls and optional cross-checking. | architecture direction confirmed |
| Result data / settlement | Resolve final match/result state independently from normal quotation ingestion where practical. | football-data.org, OpenLigaDB, API-Football, and a score endpoint from The Odds API are user-provided candidates. | candidates supplied; first adapter selection pending |
| Bank / account data | Connect balances, transactions, sandbox execution, and later account-backed flows through the bank adapter boundary. | bunq is the first concrete bank integration. Automated transactions and official sandbox operation have already been checked by the user in bunq documentation/SDK. | capability direction confirmed; adapter implementation pending |
| Notifications | Notify the user when an opportunity, approval, or capital action requires attention instead of automatically navigating a provider website. | Email first. | confirmed direction |
| Pipeline observability | Add established pipeline monitoring/metrics tooling around the existing structured Monitoring plane. | Prometheus-compatible metrics; additional tools to be selected. | confirmed direction |
| Execution adapters | Prefer official APIs where supported; otherwise keep the Phase 3 flow notification/manual-action-first. | Provider-specific execution choices still pending. | pending input |
| Performance validation | Add repeatable performance/load measurements and operator-driven test runs against the connected system, not only unit/integration test execution. | Tooling to be selected as needed. | confirmed direction |
| GUI/product experience | Continue incremental visual refinement, animations, interaction polish, and clearer product surfaces while integrations are added. | Existing Django GUI remains the product surface. | ongoing |
| Cloud runtime | Keep the current Vercel + Supabase production baseline while Phase 3 validates integrations. Broader container orchestration and per-user cloud isolation are Phase 4 concerns. | Vercel + Supabase current baseline. | existing |

## Data Source Roles

The same external provider does not have to serve every pipeline responsibility. Phase 3 should classify adapters by role and compose them deliberately.

### Data Aggregation

Data Aggregation performs the broader read-only discovery/import work that produces normalized market candidates for the engines.

- **The Odds API** — selected and connected first read-only adapter for structured pre-match/live quotation ingestion.
- **Odds-API.io** — cost-conscious candidate for structured German/international bookmaker coverage.
- **OddsPapi** — lightweight candidate for development and ingestion testing.
- **BetBurger** — professional scanner/data candidate for higher-frequency arbitrage-oriented inputs where suitable API/data access exists.
- **OddsJam** — commercial aggregated-odds candidate where its data/API offering fits the adapter contract.

Source selection should prefer useful free tiers, free test quotas, sandbox/test access, or low-cost capacity before consuming more expensive sources. The pipeline should normalize/deduplicate provider data so additional sources can be added without changing engine contracts.

### RequestHandler Revalidation

`RequestHandler` is not another broad ingestion crawler. It performs targeted refreshes for opportunities that have already survived earlier pipeline stages.

- Reuse one or more configured quotation adapters for the exact event/market/provider state that must be checked again.
- Prefer the freshest suitable source that still has quota/capacity available.
- A second source may be used as a cross-check when the opportunity value, data confidence, or configured policy justifies it.
- Revalidation remains event-specific and should not trigger an unnecessary full-market refresh.

### Result / Settlement Sources

Result adapters determine match completion/finality and validated settlement input.

- **football-data.org** — candidate for football fixtures/results.
- **OpenLigaDB** — candidate especially for German league result settlement.
- **API-Football** — candidate for richer match status/result information.
- **The Odds API score endpoint** — candidate for same-provider score/result retrieval where useful.

Result ingestion remains a separate role even when the selected quotation provider also exposes scores. This lets Q-Bet choose an independent settlement source or cross-check when useful without coupling ledger settlement to the quotation polling budget.

## Source Balancing And Smart Polling

Phase 3 should add a small source-balancing policy instead of treating every configured API equally on every cycle.

- Prefer free/test quota and lower-cost sources when their freshness/coverage is sufficient; escalate to another source only when the opportunity or required confidence justifies it.
- Polling becomes more targeted as an event approaches and as an opportunity becomes more relevant. A representative configurable cadence may tighten from roughly T-3d to T-1d, T-12h, and T-1h rather than continuously requesting the same market.
- Final pre-execution revalidation remains separate from ordinary aggregation.
- After the final useful pre-match check, a user notification may be scheduled with configurable jitter inside a short window (for example within the following ~40 minutes) rather than every qualifying opportunity producing an identical notification timestamp, provided the opportunity is still valid when acted on.
- Exact timings belong to configuration and later performance/provider-limit tuning; the architecture requirement is adaptive, quota-aware, opportunity-focused polling rather than fixed high-frequency crawling.

## User-Provided Historical Candidate Notes

The following descriptions were supplied from an earlier project note. They are useful for prioritization, but current pricing, quota, coverage, and transport details should be checked only for the provider actually selected for implementation.

### Market / Odds Sources

- **The Odds API** — intended as a primary candidate for pre-match/live odds ingestion.
- **Odds-API.io** — intended as a cost-conscious structured odds source for German and international bookmakers.
- **OddsPapi** — intended as a lightweight candidate for development/pipeline test data.
- **BetBurger** — intended as a professional scanner/data candidate for high-frequency live and pre-match arbitrage use cases.
- **OddsJam** — intended as a commercial aggregated odds candidate with broad bookmaker coverage.

No claim in this register about current WebSocket availability, exact free-tier limits, pricing, bookmaker coverage, or commercial/API access is considered verified merely because it appeared in the historical note.

### Result Sources

- **football-data.org** — intended for football result/fixture settlement data.
- **OpenLigaDB** — intended especially for German league result settlement.
- **API-Football** — intended for richer football match status and result data.
- **The Odds API score endpoint** — intended as a possible same-provider settlement source where appropriate.

### Bank / Account Integration

- **bunq** is the first concrete bank/account integration and the user already has a personal bunq account available for controlled development work.
- The user has already checked bunq documentation/SDK and confirmed that automated transactions are supported and that bunq provides an official sandbox suitable for repeated development/test setups. Phase 3 tickets should use the official documentation/SDK for the concrete authentication, endpoint, and sandbox setup mechanics instead of reopening the capability question.
- The first bank adapter should deliberately exercise the official bunq sandbox end to end: account/balance/transaction reads plus automated sandbox transactions and the resulting ledger/monitoring state. Sandbox transaction automation is part of the intended test surface, not something to suppress with live-money guardrails.
- The existing personal account can be connected separately when a Phase 3 ticket needs real account data or a user-approved account-backed flow; sandbox and personal-account modes must remain explicit configuration boundaries.
- The expected repository/deployment secret name for the existing credential is `API_KEY_BUNQ`. Only the secret reference/name may appear in code or documentation; the value must never be printed, committed, logged, copied into issues, or embedded in test data.
- The personal payment/share link is intentionally not stored in the repository because it is not required for the technical adapter contract.

## Deferred To Phase 4

- broader production cloud architecture and orchestration such as Kubernetes where justified
- isolated per-user runtime/pipeline contexts, bankrolls, credentials, and operational state
- higher-automation execution paths, including explicitly permitted browser execution where an official API is unavailable
- graceful engine shutdown/drain semantics for live work: stop new work without abandoning already-dispatched or unsettled positions
- production-scale reliability, recovery, security, and high-availability hardening

## Remaining Selection Work

A ticket agent should use the candidates above directly. Do not open broad provider-discovery work. Instead, when implementation approaches an area, select or ask the user to select the preferred candidate and verify only the unresolved facts needed to implement it.

### Market / Odds Priority

- First adapter: The Odds API, connected through `src/qbet/data/the_odds_api.py`.
- For later providers, check only implementation facts that can change over time: authentication, current transport/API shape, rate limits/quota, relevant market coverage, pricing, and permitted integration use.

### Result Source Priority

- Preferred first settlement adapter: not selected yet from football-data.org, OpenLigaDB, API-Football, and The Odds API score endpoint.
- After selection, check competition coverage, result finality/status semantics, update timing, authentication, rate limits, and settlement suitability.

### Bank / Account Integration

- First adapter: bunq.
- First test environment: official bunq sandbox.
- Credential reference: `API_KEY_BUNQ` through secret/environment handling only.
- Capability direction already established by the user: automated transactions plus sandbox operation are supported; consult bunq documentation/SDK for exact implementation mechanics.

### Monitoring / Metrics Stack

- Prometheus-compatible metrics are in scope.
- Additional tooling: TBD.

### Notification Delivery

- Email is the initial channel.
- Provider/service: TBD.

### Provider / Exchange Execution APIs

- TBD.
