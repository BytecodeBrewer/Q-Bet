# Phase 3 Integration Register

This file is the companion register for concrete Phase 3 external integrations. It stores user-approved or user-provided integration choices so ticket and development agents do not repeatedly research providers that have already been selected.

`docs/expectation-model.md` remains the authoritative architecture and roadmap document. This register supplies concrete Phase 3 integration inputs only; it does not override architecture contracts.

## Agent Rules

- Prefer an explicit user-provided integration entry over fresh provider discovery.
- Do not replace a named provider, API, bank, or monitoring tool with an alternative unless the user asks for comparison or research.
- Research is required only when the user explicitly requests it or an entry is marked `research needed`.
- Record only capabilities, intended role, known constraints, and implementation status. Never store credentials, tokens, passwords, account numbers, session secrets, or private keys here.
- External integrations must use permitted interfaces and documented provider flows. Browser automation is not a quotation-scraping channel.
- Phase 3 is notification-first for execution paths that do not have an approved direct execution API. Higher-automation browser execution is deferred to Phase 4.

## Confirmed Phase 3 Direction

| Area | Phase 3 intent | Concrete choice | Status |
| --- | --- | --- | --- |
| Market / odds data | Connect real external quotation/data sources through typed adapters. | To be supplied by user. | pending input |
| Result data / settlement | Connect separate result sources where practical so settlement does not depend on quotation polling. | To be supplied by user. | pending input |
| Bank / account data | Connect a bank/account boundary, beginning with supported read-only, sandbox, or approval-based capabilities rather than unrestricted money movement. | To be supplied by user. | pending input |
| Notifications | Notify the user when an opportunity, approval, or capital action requires attention instead of automatically navigating a provider website. | Email first. | confirmed direction |
| Pipeline observability | Add established pipeline monitoring/metrics tooling around the existing structured Monitoring plane. | Prometheus-compatible metrics; additional tools to be selected. | confirmed direction |
| Execution adapters | Prefer official APIs where supported; otherwise keep the Phase 3 flow notification/manual-action-first. | Provider-specific choices pending. | pending input |
| Performance validation | Add repeatable performance/load measurements and operator-driven test runs against the connected system, not only unit/integration test execution. | Tooling to be selected as needed. | confirmed direction |
| GUI/product experience | Continue incremental visual refinement, animations, interaction polish, and clearer product surfaces while integrations are added. | Existing Django GUI remains the product surface. | ongoing |
| Cloud runtime | Keep the current Vercel + Supabase production baseline while Phase 3 validates integrations. Broader container orchestration and per-user cloud isolation are Phase 4 concerns. | Vercel + Supabase current baseline. | existing |

## Deferred To Phase 4

- broader production cloud architecture and orchestration such as Kubernetes where justified
- isolated per-user runtime/pipeline contexts, bankrolls, credentials, and operational state
- higher-automation execution paths, including explicitly permitted browser execution where an official API is unavailable
- graceful engine shutdown/drain semantics for live work: stop new work without abandoning already-dispatched or unsettled positions
- production-scale reliability, recovery, security, and high-availability hardening

## Pending User Inputs

Add concrete entries here as they are chosen. A ticket agent should use these entries directly instead of opening broad research work unless the entry explicitly asks for it.

### Market / Odds Sources

- TBD

### Result Sources

- TBD

### Bank / Account Integration

- TBD

### Monitoring / Metrics Stack

- Prometheus-compatible metrics are in scope.
- Additional tooling: TBD

### Notification Delivery

- Email is the initial channel.
- Provider/service: TBD

### Provider / Exchange Execution APIs

- TBD
