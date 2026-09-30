# Phase 3 Web Security Review

This document records the adversarial web-surface review performed for GitHub issue #195. It describes current server-side boundaries without publishing credentials, exploit payloads, provider identifiers, or private account data.

The review treats the Phase 3 Django application as Internet-exposed and assumes an authenticated normal user can inspect routes, craft requests manually, alter identifiers and form values, replay requests, and attempt staff-only or financial-adjacent actions.

## Route and authority classification

| Surface | Methods | Authority | Server-side boundary |
| --- | --- | --- | --- |
| `/` | GET | Public | Presentation only |
| `/health/` | GET | Public | Coarse readiness and immutable release metadata only |
| `/metrics/` | GET | Machine | Bearer token; otherwise 404 |
| `/internal/execution/tick/` | POST | Machine | Bearer token, bounded expiry maintenance for pending Execution approvals/manual actions, POST-only; intentionally CSRF-exempt because it is not a browser session endpoint |
| `/internal/polling/tick/` | POST | Machine | Bearer token, bounded Smart Polling work, POST-only; intentionally CSRF-exempt because it is not a browser session endpoint |
| Registration | GET, POST | Public | Django CSRF on POST; account starts inactive |
| Verification pending | GET | Public | Read-only |
| Email verification link | GET, POST | Public/token holder | GET validates and renders confirmation only; CSRF-protected POST revalidates under row locks and activates |
| Login | GET, POST | Public | Django authentication form, CSRF on POST, session key rotation |
| Logout | POST | Authenticated | Django CSRF and session termination |
| Password change | GET, POST | Authenticated | Django password validation and CSRF |
| Password reset | GET, POST | Public/token holder | Non-enumerating request flow; Django one-time reset token and CSRF |
| Profile/preferences | GET, POST | Authenticated owner | Current user derived from `request.user`; verified email cannot be replaced through the profile form |
| Avatar read | GET | Authenticated owner | Private authenticated storage path |
| Avatar upload/remove | POST | Authenticated owner | CSRF, size/type/dimension validation, normalized image output |
| Notifications | GET | Authenticated owner | Inbox query scoped by username |
| Notification read | POST | Authenticated owner | CSRF and owner-scoped task update |
| Bonus Offers | GET, POST | Authenticated owner | Query/create ownership derived from `request.user`; return target validated as a same-host path |
| Provider Accounts | GET, POST | Authenticated owner | Owner scope derived server-side; client cannot forge verification authority |
| Dashboard / engine detail | GET | Authenticated | Read-only product views |
| Dashboard layout | POST | Authenticated owner | CSRF; user session/preferences only |
| Portfolio | GET | Authenticated owner/staff | Owner-scoped read model; no implicit ledger mutation |
| Central-bank refresh | POST | Authenticated owner | Read-only bank observation; no transfer or reconciliation |
| Provider capital location update | POST | Authenticated owner | Owner-scoped state with explicit Execution-ledger access requirement |
| Capital approvals | GET | Authenticated owner | Owner-scoped read |
| Capital decision / manual performed | POST | Authenticated owner | CSRF, owner check, approval lifecycle; performed acknowledgement remains pending reconciliation |
| Execution approvals | GET | Authenticated owner | Read-only approval and manual-action projections. Expired records are hidden without mutating Execution, queue, Monitoring, or ledger state |
| Execution decision | POST | Authenticated owner | CSRF, owner check, expiry handling, revalidation remains required before dispatch |
| Engine runtime controls | POST | Staff | Explicit staff check, typed engine/mode/action |
| Sandbox Execution controls | POST | Staff | Explicit staff check, supported-engine/action validation |
| Simulation page | GET | Staff | Staff-only control plane |
| Simulation start/run/stop | POST | Staff | Staff check, CSRF, run ownership/boundary checks |
| Pipeline dry-run | POST | Staff | Staff-only deterministic test path |
| Presentation settings | GET, POST | Authenticated owner | User-scoped preferences and CSRF |
| User engine preferences | POST | Authenticated owner | User intent remains inside global routing guardrails |
| Provider activity | GET | Authenticated | Generic customer-safe snapshot; correlation-scoped filtering is staff-only |
| Reports/history | GET | Authenticated owner/staff | Normal users are constrained by `CustomerReportAccess` before detail/export |
| Customer report exports | GET | Authenticated owner/staff | Business-only export model; no internal workflow payload |
| Monitoring | GET | Staff | Staff-only operational data |
| Monitoring exports | GET | Staff | Staff-only and sensitive references are redacted |
| Admin/control center | GET | Staff | Server-side staff predicate |
| Routing settings | GET, POST | Staff | Staff-only persisted control configuration |
| Smart Polling settings | GET, POST | Staff | Staff-only, typed configuration and reviewed-preview save boundary |
| Simulation availability | POST | Staff | Staff-only, CSRF, server-side transition checks |
| `/admin/` | Django Admin methods | Staff/permissioned | Django Admin authentication and model permissions |
| Account boundary | GET | Authenticated | Read-only authenticated boundary |

Django's global CSRF middleware protects browser-session state changes. The execution and polling ticks are the intentional `csrf_exempt` Q-Bet machine routes found in the current review; both are POST-only and protected by constant-time bearer-token comparison rather than browser-session CSRF.

## Findings fixed in this ticket

### Hosted runtime hardening covered Preview but not Production

The previous settings tied secret-key enforcement, `DEBUG=false`, PostgreSQL SSL, SMTP defaults, and deployment assumptions to `QBET_HOSTED_PREVIEW`. Production explicitly set that flag false.

The hosted boundary now covers both Vercel Preview and Production through `QBET_HOSTED_RUNTIME` plus Vercel environment detection. Hosted runtime startup fails closed when:

- the local development secret would be used;
- `DEBUG` is enabled;
- no exact allowed host can be established;
- a wildcard or suffix-wide host such as `*` or `.vercel.app` is configured.

The exact current Vercel deployment host may be added from `VERCEL_URL`. The stable Q-Bet alias remains an explicit deployment allowlist entry. Hosted PostgreSQL forces SSL.

Browser/session hardening is explicit:

- Secure session and CSRF cookies in hosted runtime;
- HttpOnly session and CSRF cookies;
- SameSite=Lax;
- bounded 12-hour default session lifetime;
- HTTPS redirect behind Vercel's forwarded-proto boundary;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`;
- `Referrer-Policy: no-referrer`;
- same-origin opener policy;
- Production HSTS for one year;
- CSP with same-origin defaults, denied framing/objects, and current inline compatibility;
- Permissions-Policy denying camera, microphone, geolocation, payment, and USB by default;
- bounded request/upload memory above the current five-megabyte avatar file envelope.

HSTS subdomain and preload flags are deliberately disabled. Q-Bet does not own the parent `vercel.app` domain, so claiming its subdomains or preload scope would be inappropriate.

### Email verification mutated state on GET

A GET to a valid verification link previously activated the account. Mail scanners and link-prefetch systems can issue GET requests without user intent.

GET now only validates the token and renders a confirmation page. Account activation requires a CSRF-protected POST and repeats token/expiry checks while holding the existing database row locks.

Expired-registration cleanup also no longer runs from safe GET requests or request middleware. Cleanup occurs only inside the CSRF-protected registration/verification POST views, so a rejected CSRF request cannot trigger account deletion.

### Execution approval listing mutated state on GET

`ExecutionApprovalService.pending_for()` previously reconciled expired approvals while rendering the approval list. Reading the page could therefore cancel Execution and queue state.

The approval projection is now read-only. Expired approvals are omitted from the list and navigation count without any persistence change. The later-added manual Execution projection follows the same rule: expired manual actions are hidden on GET without terminalizing state. The protected `POST /internal/execution/tick/` runs a single bounded maintenance budget across expired approvals and manual actions, terminalizing their authoritative Execution/queue state without provider dispatch. The authoritative user POST decision boundary also expires an approval fail-closed at or after its deadline.

### Customer correlation probing

A normal authenticated user could submit an arbitrary Monitoring correlation UUID to `/activity/provider/` and obtain a reduced but still useful state/timing oracle.

The generic customer-safe provider activity snapshot remains available. Correlation-scoped provider activity is now staff-only and crafted normal-user requests receive 404.

### Request-method ambiguity

Read-only project views now declare GET explicitly, mixed form views declare GET/POST explicitly, and existing state-changing actions remain POST-only. This reduces accidental state expansion through unsupported HTTP verbs and makes the route contract reviewable.

## Ownership and data-exposure result

The current Phase 3 owner-scoped boundaries remain server-side:

- Bonus Offers and Provider Accounts derive the owner from `request.user`;
- Execution and Capital approval decisions reject another user's identifiers;
- customer report detail and every customer export check `CustomerReportAccess`;
- normal users cannot read Monitoring or invoke Simulation/control-plane actions;
- portfolio/provider state is user-scoped unless an explicit staff path applies.

Reviewed active templates, static JavaScript, customer exports, Monitoring exports, health output, and structured request logging do not intentionally expose API keys, bank credentials, session identifiers, bearer tokens, raw verification/reset tokens, or IBAN data. Account-token URL paths are redacted before request logging. Monitoring exports retain credential-like reference redaction.

The public health endpoint intentionally exposes only coarse service/readiness fields and an immutable release SHA used by deployment verification. It does not expose database URLs, provider credentials, internal exceptions, or session state.

## Financial and execution authority

No change in this ticket increases execution or money authority.

Browser requests cannot directly bypass the canonical boundaries:

`Domain Risk -> LiquidityChecker -> Approval -> pre-execution revalidation -> Execution -> Settlement -> PortfolioLedger`.

Simulation and Execution remain separate. Execution approval does not itself submit an external order. Capital-movement performed acknowledgement does not credit or debit the authoritative ledger until reconciliation. bunq remains read-only/sandbox according to configured mode. No provider order, bank transfer, deposit, withdrawal, or credential-dependent action is added by this review.

## Residual risks and explicit non-claims

The following are not hidden behind local placebo controls:

- **MFA/2FA is not implemented.** Phase 4 must add MFA or step-up authentication for privileged users and higher-authority Execution actions before authority expands.
- **Distributed authentication abuse/rate limiting is not implemented by Q-Bet.** A per-process in-memory throttle would be ineffective on serverless Vercel instances. Broader exposure needs an edge/shared-store rate-limit and abuse-control design.
- **CSP is not yet nonce/hash strict.** The current Django/Admin shell still needs limited inline compatibility. Phase 4 should remove `unsafe-inline` where the frontend permits it.
- **Dependency CVE auditing is not yet an enforced repository gate.** Normal dependency updates still apply, but a supported `pip-audit`/SBOM-style CI control should be introduced deliberately rather than claiming the dependency graph is vulnerability-free.
- This is an internal hardening review, not third-party penetration testing, compliance certification, or a guarantee of absence of vulnerabilities.

## Phase 4 security handoff

Future browser-agent, multi-user and higher-authority work must preserve or strengthen:

- per-user Execution and browser/runtime contexts;
- per-user provider session and credential isolation;
- financial-account and PortfolioLedger isolation;
- secret isolation in deployment/runtime infrastructure;
- MFA/step-up authentication for privileged and high-authority actions;
- staff global availability, user account-scoped intent, and per-operation approval as separate controls;
- Domain Risk, LiquidityChecker, Approval, pre-execution revalidation and Settlement as non-bypassable server-side authority boundaries;
- idempotency, restart recovery and audit logging for every irreversible transition;
- no CAPTCHA, fingerprinting, anti-bot or provider-control bypass.

Browser automation, Kubernetes/cloud isolation and real-money automation remain outside this Phase 3 ticket.
