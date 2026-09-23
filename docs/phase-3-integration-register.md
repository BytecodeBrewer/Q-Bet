# Phase 3 Integration Register

This file is the companion register for concrete Phase 3 external integrations. It stores user-approved or user-provided integration choices so ticket and development agents do not repeatedly research providers that have already been selected.

`docs/expectation-model.md` remains the authoritative architecture and roadmap document. `docs/phase-3-missing-points.md` is the Phase 3 completion plan. This register supplies concrete integration inputs and current implementation status only; it does not override architecture contracts.

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
| Data Aggregation / odds | Feed normalized candidate markets and quotations into the sports pipeline through typed adapters. | The Odds API is the selected first read-only quotation adapter; Odds-API.io, OddsPapi, BetBurger, and OddsJam remain future candidates. | The Odds API adapter is connected to GUI-started SportsCapital Simulation through normalized snapshots and the Sports Match Builder |
| RequestHandler revalidation | Perform targeted event-specific refreshes close to action time using current quotation/provider state rather than repeating broad ingestion. | Reuse selected quotation adapters with source-aware targeted pulls and optional cross-checking. | real The Odds API-backed targeted Execution revalidation implemented through the durable dispatch path |
| Result data / settlement | Resolve final match/result state independently from normal quotation ingestion where practical. | The Odds API v4 scores endpoint is the first connected result source; the other user-provided providers remain future candidates. | exact-event score/finality collection is connected to the post-event SettlementService boundary |
| Bank / account data | Connect balances, transactions, sandbox execution, and later account-backed flows through the bank adapter boundary. | bunq is the first concrete bank integration. | bunq read-only + official sandbox adapter + durable idempotent Simulation funding feedback implemented |
| Notifications | Notify the user when an opportunity, approval, or capital action requires attention instead of automatically navigating a provider website. | Email first. | notification domain + Django email transport implemented; richer preferences/inbox later |
| Pipeline observability | Add established infrastructure metrics around the existing structured Monitoring plane without creating a second business-monitoring authority. | Durable PostgreSQL-backed Prometheus-compatible metrics plus the repository-owned Grafana dashboard in `dashboards/qbet-observability.json`. | implemented: protected metrics endpoint, bounded operational metrics, Grafana contract, Monitoring presets/summaries, degraded-source state and optional staff infrastructure links |
| Execution adapters | Prefer official APIs where supported; otherwise keep the Phase 3 flow notification/manual-action-first. | Provider-specific execution choices still pending. | sandbox bank execution boundary exists; provider execution integrations pending |
| Performance validation | Add repeatable performance/load measurements and operator-driven test runs against the connected system, not only unit/integration test execution. | Dedicated internal connected baseline first; provider/network hot paths later. | Phase 3 internal performance baseline implemented |
| GUI/product experience | Continue incremental visual refinement, animations, interaction polish, and clearer product surfaces while integrations are added. | Existing Django GUI remains the product surface. | functional controls exist; larger Settings/Admin/UX expansion later in Phase 3 |
| Cloud runtime | Keep the current Vercel + Supabase production baseline while Phase 3 validates integrations. Broader container orchestration and per-user cloud isolation are Phase 4 concerns. | Vercel + Supabase current baseline. | existing |

## Sportsbook And Exchange Semantics

- Fixed-odds sportsbook market/quotation data remains API-first.
- BonusEngine promotion/account metadata is a separate input boundary; no universal promotion API is assumed.
- A later Phase 3 provider-specific read-only browser adapter may supply promotion/account metadata from the user's own configured sportsbook account where no supported API exists.
- Betting exchanges are peer-to-peer venues and belong to the separate planned `SportsExchangeEngine`; sportsbook brands such as Tipico or Bwin are not exchange counterparties exposed through an exchange API.
- Betfair currently states that its Exchange is unavailable to customers in Germany, and its API does not expose Exchange markets from German locations. This is an integration constraint, not a generic engine definition.
  - https://support.betfair.com/de/app/answers/detail/a_id/5939/
  - https://support.developer.betfair.com/hc/en-us/articles/360004831131-Why-do-markets-not-appear-in-the-listEvents-listMarketCatalogue-or-listMarketBook-API-response
- Controlled sportsbook browser order placement remains a later Phase 4 authority increase behind approval, revalidation, risk, liquidity, and execution controls.

## Data Source Roles

The same external provider does not have to serve every pipeline responsibility. Phase 3 should classify adapters by role and compose them deliberately.

### Data Aggregation

Data Aggregation performs the broader read-only discovery/import work that produces normalized market candidates for the engines.

- **The Odds API** — selected and connected first read-only adapter for structured pre-match/live quotation ingestion; GUI-started SportsCapital Simulation consumes one configured two-outcome event/market through normalized snapshots and the Sports Match Builder.
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
- The targeted Execution path uses a real The Odds API-backed RequestHandler implementation and preserves request context through the durable queue/dispatch boundary.

### Result / Settlement Sources

Result adapters determine match completion/finality and validated settlement input.

- **football-data.org** — candidate for football fixtures/results.
- **OpenLigaDB** — candidate especially for German league result settlement.
- **API-Football** — candidate for richer match status/result information.
- **The Odds API v4 scores endpoint** — selected first result adapter. It performs exact-event read-only score/finality collection through explicit sport/event identity and feeds only validated provider-neutral evidence into the post-event settlement boundary.

Result ingestion remains a separate role even when the selected quotation provider also exposes scores. This lets Q-Bet choose an independent settlement source or cross-check when useful without coupling ledger settlement to the quotation polling budget. Result polling should only continue for actions that actually need settlement tracking.

## Source Balancing And Smart Polling

The provider-neutral `SmartPollingPolicy` resolves persisted provider/target/engine strategies with configurable refresh points, freshness, bounded attempts, quota/capacity metadata, cost class and staff-managed enablement. Final Execution revalidation remains a separate RequestHandler concern.

Automatic balancing across multiple quotation providers remains a later slice to introduce when another real source is connected; the current persisted strategy boundary already allows plan/capacity changes without editing Python constants.

- Prefer free/test quota and lower-cost sources when their freshness/coverage is sufficient; escalate to another source only when the opportunity or required confidence justifies it.
- Polling becomes more targeted as an event approaches and as an opportunity becomes more relevant. A representative configurable cadence may include T-24h, T-12h, T-1h or other administrator-defined points rather than continuously requesting the same market.
- Final pre-action revalidation remains separate from ordinary aggregation.
- Provider/engine strategy must account for current rate limits, quota/subscription capacity, cost and required freshness.
- Exact timings are persisted/configurable, so changing an API plan or configured capacity does not require Python code changes.

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

- **bunq** is the first concrete bank/account integration; read-only access, the official sandbox adapter, and restart-safe idempotent Simulation funding feedback into Portfolio Ledger are implemented.
- The user has already checked bunq documentation/SDK and confirmed that automated transactions are supported and that bunq provides an official sandbox suitable for repeated development/test setups. Phase 3 tickets should use the official documentation/SDK for concrete authentication, endpoint, and sandbox mechanics instead of reopening the capability question.
- The adapter supports account/balance reads and guarded fake-money sandbox payment execution, and successful correlated sandbox funding feedback is persisted into the Simulation Portfolio Ledger exactly once. The next architecture task is to exercise this accepted boundary inside the genuine connected E2E flow.
- The existing personal account can be connected separately when a Phase 3 ticket needs real account data or a user-approved account-backed flow; sandbox and personal-account modes must remain explicit configuration boundaries.
- The expected repository/deployment secret name for the existing credential is `API_KEY_BUNQ`. Only the secret reference/name may appear in code or documentation; the value must never be printed, committed, logged, copied into issues, or embedded in test data.
- The personal payment/share link is intentionally not stored in the repository because it is not required for the technical adapter contract.

## Deferred To Phase 4

- broader production cloud architecture and orchestration such as Kubernetes where justified
- isolated per-user runtime/pipeline contexts, bankrolls, credentials, and operational state
- higher-automation execution paths, including explicitly permitted browser execution where an official API is unavailable
- graceful engine shutdown/drain semantics for live work: stop new work without abandoning already-dispatched or unsettled positions
- production-scale reliability, recovery, security, and high-availability hardening
- broader live-capital and multi-user execution architecture

## Remaining Selection Work

A ticket agent should use the candidates above directly. Do not open broad provider-discovery work. Instead, when implementation approaches an area, select or ask the user to select the preferred candidate and verify only the unresolved facts needed to implement it.

### Market / Odds Priority

- First adapter: The Odds API, connected through `src/qbet/data/the_odds_api.py`.
- For later providers, check only implementation facts that can change over time: authentication, current transport/API shape, rate limits/quota, relevant market coverage, pricing, and permitted integration use.

### Result Source Priority

- First settlement adapter: The Odds API v4 scores endpoint, connected through exact sport/event identity to the provider-neutral result boundary and post-event SettlementService.
- football-data.org, OpenLigaDB and API-Football remain later candidates when coverage, independent cross-checking or richer status semantics justify another adapter.

### Bank / Account Integration

- First adapter: bunq, implemented in read-only and official sandbox modes.
- First test environment: official bunq sandbox.
- Credential reference: `API_KEY_BUNQ` through secret/environment handling only.
- Next integration objective: compose the accepted bunq sandbox funding feedback with the connected market-data and post-event result boundaries in the genuine Phase 3 E2E gate.

### Monitoring / Metrics Stack

- The selected Phase 3 stack is Q-Bet's existing PostgreSQL Monitoring plane plus a durable read-only Prometheus-compatible projection and Grafana for infrastructure visualization.
- `/metrics/` is disabled unless `QBET_METRICS_TOKEN` is configured. A scraper supplies that deployment secret as a Bearer token; the value is never committed or rendered in the application.
- Import `dashboards/qbet-observability.json` into Grafana and select the Prometheus datasource through the dashboard's `${datasource}` variable. The dashboard contains no environment-specific datasource ID or secret.
- Optional staff-only links are configured with `QBET_GRAFANA_URL`, `QBET_VERCEL_DASHBOARD_URL` and `QBET_SUPABASE_DASHBOARD_URL`; invalid or credential-bearing URLs are omitted.
- The metrics projection remains observational and derives activity, duration, queue and Execution lifecycle state from durable PostgreSQL records. It is never a workflow, execution or capital authority.

### Notification Delivery

- Email is the initial channel and the Django email transport exists.
- Deployment mail provider/back-end configuration remains environment-specific.
- Internal inbox, channel preferences and SMS are later Phase 3 GUI/product work.

### Provider / Exchange Execution APIs

- TBD.
