# Phase 3 Integration Register

This file is the companion register for concrete Phase 3 external integrations. It stores user-approved or user-provided integration choices so ticket and development agents do not repeatedly research providers that have already been selected.

`docs/expectation-model.md` remains the authoritative architecture and roadmap document. This register supplies concrete Phase 3 integration inputs only; it does not override architecture contracts.

## Agent Rules

- Prefer an explicit user-provided integration entry over fresh provider discovery.
- Do not replace a named provider, API, bank, or monitoring tool with an alternative unless the user asks for comparison or research.
- Research is required only when the user explicitly requests it or an entry is marked `research needed`.
- Treat historical capability, pricing, free-tier, transport, and coverage descriptions as unverified until a Phase 3 ticket checks the current provider documentation.
- Record only capabilities, intended role, known constraints, and implementation status. Never store credentials, tokens, passwords, account numbers, session secrets, payment links, private keys, or other unnecessary personal account identifiers here.
- External integrations must use permitted interfaces and documented provider flows. Browser automation is not a quotation-scraping channel.
- Phase 3 is notification-first for execution paths that do not have an approved direct execution API. Higher-automation browser execution is deferred to Phase 4.
- Existing credentials must be consumed only through repository/deployment secrets or environment configuration; never copy secret values into source, issues, logs, reports, test fixtures, or documentation.

## Confirmed Phase 3 Direction

| Area | Phase 3 intent | Concrete choice | Status |
| --- | --- | --- | --- |
| Market / odds data | Connect real external quotation/data sources through typed adapters. | The Odds API, Odds-API.io, OddsPapi, BetBurger, OddsJam are user-provided candidates. | candidates supplied; capability verification pending |
| Result data / settlement | Connect separate result sources where practical so settlement does not depend on quotation polling. | football-data.org, OpenLigaDB, API-Football, and a score endpoint from The Odds API are user-provided candidates. | candidates supplied; capability verification pending |
| Bank / account data | Connect a bank/account boundary, beginning with supported read-only, sandbox, transaction visibility, or approval-based capabilities rather than unrestricted money movement. | bunq personal account available as the first concrete bank integration candidate. | account available; API capability/eligibility verification pending |
| Notifications | Notify the user when an opportunity, approval, or capital action requires attention instead of automatically navigating a provider website. | Email first. | confirmed direction |
| Pipeline observability | Add established pipeline monitoring/metrics tooling around the existing structured Monitoring plane. | Prometheus-compatible metrics; additional tools to be selected. | confirmed direction |
| Execution adapters | Prefer official APIs where supported; otherwise keep the Phase 3 flow notification/manual-action-first. | Provider-specific execution choices still pending. | pending input |
| Performance validation | Add repeatable performance/load measurements and operator-driven test runs against the connected system, not only unit/integration test execution. | Tooling to be selected as needed. | confirmed direction |
| GUI/product experience | Continue incremental visual refinement, animations, interaction polish, and clearer product surfaces while integrations are added. | Existing Django GUI remains the product surface. | ongoing |
| Cloud runtime | Keep the current Vercel + Supabase production baseline while Phase 3 validates integrations. Broader container orchestration and per-user cloud isolation are Phase 4 concerns. | Vercel + Supabase current baseline. | existing |

## User-Provided Historical Candidate Notes

The following descriptions were supplied from an earlier project note. They are useful for prioritization, but they are not current provider verification. Phase 3 tickets should verify only the selected provider's present documentation, terms, authentication, rate limits, pricing, transport support, data coverage, and permitted use before implementation.

### Market / Odds Sources

- **The Odds API** — intended as a primary candidate for pre-match/live odds ingestion.
- **Odds-API.io** — intended as a cost-conscious structured odds source for German and international bookmakers.
- **OddsPapi** — intended as a lightweight candidate for development/pipeline test data.
- **BetBurger** — intended as a professional scanner/data candidate for high-frequency live and pre-match arbitrage use cases.
- **OddsJam** — intended as a commercial aggregated odds candidate with broad bookmaker coverage.

No claim in this register about WebSocket availability, free tiers, exact pricing, bookmaker coverage, or commercial/API access is considered verified yet.

### Result Sources

- **football-data.org** — intended for football result/fixture settlement data.
- **OpenLigaDB** — intended especially for German league result settlement.
- **API-Football** — intended for richer football match status and result data.
- **The Odds API score endpoint** — intended as a possible same-provider settlement source where appropriate.

The architecture still prefers result-data independence where practical so quotation polling is not automatically the settlement dependency.

### Bank / Account Integration

- **bunq** is the first concrete bank/account candidate because the user already has a personal bunq account available for controlled development work.
- A bunq API credential is already provisioned through GitHub secret/environment handling. The secret value must never be read into documentation, echoed, logged, committed, or copied into tickets.
- The personal payment/share link is intentionally not stored in the repository because it is not required for the technical adapter contract.
- Before implementation, verify the current bunq API capabilities and the permissions/eligibility of the existing personal account for the intended Phase 3 operations.
- Phase 3 should begin with the lowest-authority useful capability supported by the account/API: account metadata, balances, transactions, sandbox/test capability, or approval-based actions. Do not assume unrestricted payment initiation.

## Deferred To Phase 4

- broader production cloud architecture and orchestration such as Kubernetes where justified
- isolated per-user runtime/pipeline contexts, bankrolls, credentials, and operational state
- higher-automation execution paths, including explicitly permitted browser execution where an official API is unavailable
- graceful engine shutdown/drain semantics for live work: stop new work without abandoning already-dispatched or unsettled positions
- production-scale reliability, recovery, security, and high-availability hardening

## Remaining Selection Work

A ticket agent should use the candidates above directly. Do not open broad provider-discovery work. Instead, when implementation approaches an area, select or ask the user to select the preferred candidate and verify only the unresolved facts needed to implement it.

### Market / Odds Priority

- Preferred first adapter: not selected yet from The Odds API, Odds-API.io, OddsPapi, BetBurger, and OddsJam.
- Verification required after selection: current API access, permitted use, authentication, transport, rate limits, coverage, pricing, and fixture suitability.

### Result Source Priority

- Preferred first settlement adapter: not selected yet from football-data.org, OpenLigaDB, API-Football, and The Odds API score endpoint.
- Verification required after selection: competition coverage, result finality/status semantics, update timing, authentication, rate limits, and settlement suitability.

### Bank / Account Integration

- First concrete candidate: bunq personal account.
- Credential handling: secret/environment only; never repository content.
- Verification required: account/API eligibility, available read/write scopes, sandbox/test support, transaction/balance endpoints, approval requirements, rate limits, and any restrictions relevant to the intended Q-Bet workflow.

### Monitoring / Metrics Stack

- Prometheus-compatible metrics are in scope.
- Additional tooling: TBD.

### Notification Delivery

- Email is the initial channel.
- Provider/service: TBD.

### Provider / Exchange Execution APIs

- TBD.
