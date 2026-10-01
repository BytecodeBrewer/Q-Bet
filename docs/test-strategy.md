# Q-Bet Test Strategy And Regression Ownership

## Purpose

The test suite should make failures local and explain which architectural boundary regressed. A larger end-to-end test proves that components compose; it does not replace focused tests for calculations, recovery, idempotency, authorization, provider errors, or capital safety.

Normal CI is offline and credential-free. Real external sandbox or read-only verification remains explicitly opt-in.

## Phase 3 Final Consolidation Baseline

Issue #196 treats the suite as a moving final Phase 3 baseline while the remaining product/security tickets land. The starting point for this consolidation is `develop` commit `991bfeac2c20e5d461a6739ec1660f56d9992425` on 2026-09-30:

- 879 deterministic tests passed and 2 were skipped;
- the standard Pytest step completed in 147.29 seconds on GitHub Actions;
- 143 files existed under `tests/`;
- 183 Python files existed under `src/`;
- the canonical connected gate remained `tests/e2e/test_phase3_connected_sports_capital.py`.

These are audit-start metrics. #196 proceeds against the current `develop` baseline; if another Phase 3 ticket lands later or Reviewer feedback changes the product surface, the same consolidation is reconciled again rather than keeping a competing baseline.

The consolidation loop is deliberate:

1. establish repository-wide coverage measurement and the CI gate;
2. use measured uncovered lines to identify real regression gaps;
3. remove or merge redundancy where the current behavioral owner is already clear;
4. reconcile against newer `develop` heads as they land;
5. refresh count, runtime, coverage and intentionally uncovered areas before each Reviewer handoff.

This permits useful consolidation now while keeping later correction rounds explicit.

## Current Test Inventory By Purpose

The current #196 working baseline after reconciling accepted #195, #209 and #213 contains **123 Python test files**. This inventory is grouped by regression purpose rather than historical ticket names:

| Group | Files | Primary ownership |
| --- | ---: | --- |
| calculation/unit | 7 | deterministic formulas, Decimal/rounding and strategy math |
| Domain Risk / ledger / operational-risk unit | 6 | policy decisions, ledger semantics and fail-closed rules |
| provider / data / bank unit | 12 | adapter contracts, normalization and deterministic external-boundary behavior |
| workflow / execution / simulation unit | 13 | routing, orchestration, execution and simulation service contracts |
| other focused unit | 23 | reporting, Monitoring, notifications, storage/web helpers and CI contracts |
| PostgreSQL integration | 7 | durable repositories, recovery, idempotency, row-level persistence guarantees |
| web integration | 33 | authentication, authorization, state-changing HTTP actions and customer/staff surfaces |
| workflow integration | 11 | queue lifecycle, approval/revalidation, dispatch, mode isolation and authoritative Simulation liquidity |
| other integration | 8 | bank, notifications, settlement and observability composition |
| connected E2E | 2 | canonical product composition plus protected bunq sandbox boundary |
| performance | 1 | Phase 3 connected performance budget |

The large web-integration group is the main duplication-audit surface. File count alone is not a cleanup target: focused authorization, security, state-transition and money-safety tests stay even when a connected E2E crosses the same modules.

## Ownership By Layer

| Concern | Primary test layer | Responsibility |
| --- | --- | --- |
| calculations, Decimal behavior and dynamic rounding | unit | deterministic formulas, edge cases, fees/taxes, liability, liquidity and stake increments |
| typed contracts and adapter payload/error mapping | unit / contract | validation, normalization, stable failure categories and secret-safe errors |
| Domain Risk and Liquidity policy | unit / focused integration | allow/recheck/reject behavior without external side effects |
| PostgreSQL repositories and authoritative recovery | integration | restart safety, transactionality, stale/conflicting writes and persistence failures |
| approval, queue claiming, rescheduling and crash recovery | integration | durable lifecycle transitions and fail-closed behavior |
| funding exactly-once | integration + one E2E proof | claim/replay semantics stay focused; the connected gate proves composition once |
| result/settlement exactly-once | integration + one E2E proof | partial/final/mismatch/replay stay focused; the connected gate proves composition once |
| full connected SportsCapital lifecycle | E2E | canonical Phase 3 composition with offline provider transports |
| external provider sandbox/read-only verification | protected opt-in E2E | explicit environment/credential boundary; never part of normal CI |
| latency/regression budget | performance | bounded timing measurements, not correctness duplication |
| web authorization and product visibility | integration/web | role boundaries and business-facing surfaces |

## Canonical Connected Gate

`tests/e2e/test_phase3_connected_sports_capital.py` is the canonical Phase 3 composition regression gate. It proves that the real Q-Bet services can form one complete business loop while external network edges stay deterministic in normal CI:

```text
market adapter
-> preparation
-> SportsCapitalEngine
-> Domain Risk
-> LiquidityChecker
-> Simulation / controlled Execution
-> sandbox funding
-> result collection
-> settlement
-> authoritative Portfolio Ledger / execution state
-> Reporting / Monitoring visibility
```

The connected gate owns the fact that this chain works together. Focused tests continue to own failure diagnosis and edge behavior inside each boundary.

## Focused Integration Ownership

The suite keeps focused integration coverage when behavior must remain directly diagnosable:

- queue claim, recheck, expiry and async scheduling;
- Simulation/Execution capital and history isolation;
- explicit approval and unauthorized/rejected decisions;
- PostgreSQL recovery and atomic state persistence;
- sandbox funding claim/replay and unknown provider outcomes;
- targeted market revalidation and stale/changed provider state;
- partial/not-yet-available result handling;
- provider/result identity mismatch;
- settlement replay and restart behavior;
- credential/raw-payload redaction;
- web authorization and persistence boundaries.

A focused test is not redundant merely because the connected E2E passes through the same module.

## Legacy Audit Decisions

The Phase 3 consolidation intentionally retires architecture-history naming:

- `tests/e2e/test_phase2_operability_gate.py` is removed. Its composition assertions are owned by the connected Phase 3 E2E; routing settings remain in `tests/integration/web/test_routing_settings.py`, approval behavior in `test_execution_approval_ui.py` / workflow approval tests, revalidation in `tests/integration/workflow/test_execution_approval_boundary.py` and `test_targeted_market_revalidation.py`, while Monitoring export/access control remain in their dedicated web integration suites.
- `tests/e2e/test_mode_settlement_isolation.py` is no longer treated as an E2E gate. Its unique queue/claim/expiry/async/result-state/isolation cases move to `tests/integration/workflow/test_mode_queue_and_isolation.py`. Duplicated single/dual-mode happy paths, immediate settlement happy paths and repeated-settlement checks are removed because current E2E/settlement tests own them.
- `tests/integration/web/test_phase2_completion_boundaries.py` is split by responsibility: owner/authorization/rejection approval scenarios move to `test_execution_approval_ui.py`, while GUI Simulation ledger recovery moves to `test_durable_simulation_and_dispatch.py`.
- `tests/integration/web/test_issue_112_review_regressions.py` becomes `tests/integration/web/test_durable_simulation_and_dispatch.py`; its surviving regressions protect shared Simulation ledger merging and persisted-owner dispatch behavior rather than one historical issue number.

Git history retains the original issue/phase context.

## Phase 3 Consolidation Audit Decisions

The #196 audit uses measured coverage and reference search to distinguish dead architecture from under-tested live behavior:

- `src/qbet/web/report_exports.py` was removed after the coverage baseline reported 0% and repository search confirmed that no production or test code imported its `SimulationReportExport` path. The active customer export boundary is `qbet.reporting.exports.CustomerReportExport`, which already owns JSON/CSV/PDF business export behavior and redaction tests. Adding tests to the unused web module would have preserved obsolete architecture rather than regression value.
- `PostgresNotificationRepository` remains live through Execution notification dispatch, so its low baseline coverage is not treated as removable code. Focused PostgreSQL integration coverage owns durable create/load/save behavior, execution-recipient idempotency across competing task ids, and fail-closed save of unknown tasks.
- `tests/integration/web/test_phase2_visuals.py` was retired as a historical mixed-responsibility suite. Public product-shell semantics stay in `test_web_shell.py`, executable Home-flow JavaScript behavior stays in `test_home_flow_runtime.py`, drag behavior stays in `test_dashboard_drag_runtime.py`, engine runtime controls stay in `test_engine_runtime_controls.py`, and Monitoring availability/failure presentation stays in `test_monitoring_exports.py`. Source-string assertions for private CSS/JavaScript structure were not preserved when an executable or public-boundary owner already existed.
- `test_gui_control_plane.py` remains only for cross-plane dashboard/Monitoring/Reporting composition that has no narrower owner. Duplicate access/visibility/preferences cases were removed because `test_monitoring_exports.py`, `test_simulation_controls.py`, `test_navigation_settings_structure.py`, and `test_auth_dashboard.py` already exercise those boundaries more directly.
- Durable capital funding workflow integrity is owned by `tests/integration/postgres/test_capital_workflow_repository.py`: attention replay/idempotency, owner-scoped listing, identity conflicts, durable metadata consistency, authenticated approval authority, and missing-ledger fail-closed behavior. The web capital-approval suite keeps only HTTP/user-facing behavior.
- The accepted Phase 3 security hardening is now owned by `test_account_security.py` and `test_security_hardening.py`; these cover token lifetime/redaction, session rotation, CSRF/method constraints, staff-only runtime controls and security headers. The earlier GUI control-plane duplicates stay removed.
- Execution approval and manual-action lifecycle ownership now stays with `test_execution_approval_ui.py`, `test_execution_approval_boundary.py`, and `test_manual_execution_lifecycle.py`. Capital approval remains a separate money-movement concern and is not merged into those execution tests.
- The accepted Bonus Offer redesign is owned by `test_bonus_offers.py`, `test_bonus_offer_simulation.py`, `test_bonus_offer_dependency_invalidation.py`, and `test_engine_notices.py`. These add promotion-term intake, duplicate handling, connected Bonus simulation, dependency invalidation and coverage/readiness presentation rather than duplicating the SportsCapital connected E2E.
- Accepted #209 shared-Simulation ownership is now explicit: `test_simulation_liquidity_concurrency.py` owns authoritative shared-capital contention and currency-mismatch behavior under the PostgreSQL row-lock boundary; `test_mode_queue_and_isolation.py` owns queue timing, claim/recheck/expiry and cross-mode isolation, without duplicating the capital-contention scenario. Web Simulation controls own seed/reset/routing/user-surface behavior, and focused unit tests own runner-level LiquidityChecker decisions.

This distinction is the cleanup rule for later passes: unused code with a superseding canonical owner is removed; live safety/persistence behavior receives a focused owner before any overlapping higher-level test is considered redundant.


## Legacy Scenario Audit

The following decisions cover every scenario from the four named legacy files in #160.

| Former scenario | Decision | Current owner |
| --- | --- | --- |
| Phase 2 operability: GUI routing modes remain deterministic | remove duplicate composition check | `test_routing_settings.py` owns persisted staff routing; connected Phase 3 E2E owns actual dual-mode composition |
| Phase 2 operability: connected success path | remove duplicate E2E | connected Phase 3 SportsCapital E2E plus focused approval/Monitoring suites |
| Phase 2 operability: rejected final revalidation | remove duplicate E2E | `test_execution_approval_boundary.py` and `test_targeted_market_revalidation.py` |
| mode isolation: single-mode schedule/dispatch | remove duplicate happy path | routing/workflow focused tests plus connected E2E |
| mode isolation: dual-mode fanout/history | remove duplicate happy path | connected Phase 3 E2E |
| mode isolation: recheck and expiry persistence | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: atomic claim | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: async wait/reschedule | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: delayed wake expiry | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: recheck cannot pass expiry | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: non-success result states | keep and move | `test_mode_queue_and_isolation.py` |
| mode isolation: Simulation-only report happy path | remove duplicate happy path | Simulation integration suite and connected Phase 3 E2E |
| mode isolation: Execution-only settle/retrieve happy path | remove duplicate happy path | post-event settlement integration plus connected Phase 3 E2E |
| mode isolation: dual-mode persisted results | remove duplicate happy path | connected Phase 3 E2E |
| mode isolation: repeated settlement delivery | remove duplicate | focused post-event/result settlement tests own replay/idempotency |
| mode isolation: cancelled Execution leaves Simulation report unchanged | keep and move | `test_mode_queue_and_isolation.py` as explicit cross-mode isolation regression |
| mode isolation: revalidation rejection before Execution state | remove duplicate | approval/revalidation integration suites |
| Phase 2 completion: GUI Simulation ledger recovery | keep and move | `test_durable_simulation_and_dispatch.py` |
| Phase 2 completion: owner-only business approval UI | keep and rename | `test_execution_approval_ui.py` |
| Phase 2 completion: rejected/unauthorized decision safety | keep and rename | `test_execution_approval_ui.py` |
| Issue 112: GUI Simulation preserves shared ledger history | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: stale Simulation snapshots merge safely | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: persisted owner used by global due claim | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: routed Simulation durable merge | keep and rename | `test_durable_simulation_and_dispatch.py` |

The earlier temporary exception for `tests/integration/web/test_phase2_visuals.py` is closed by #196. Its surviving behavior now has current owners listed above, so no Phase 2-named web regression file remains solely for historical ticket context.

## Current #196 Reconciled Result

The accepted #209 shared Simulation portfolio has now been merged into the existing #196 branch. The ownership audit is complete, including consolidation of the duplicate shared-capital contention regression into the dedicated Simulation liquidity-concurrency suite.

Current structural inventory before final exact-head measurement:

- **123 Python test files**;
- authoritative shared Simulation liquidity concurrency/currency behavior has one focused PostgreSQL integration owner;
- queue/mode-isolation coverage no longer duplicates that contention scenario;
- #195 Security/Approval and #213 Bonus Offer ownership remain unchanged;
- the PR diff remains limited to #196 consolidation/configuration/test changes plus the test consolidation required by the final #209 audit.

Final deterministic test count, runtime and coverage are intentionally recorded only after the exact-head validation below completes.

## Adding Future Regressions

Place a regression at the narrowest layer that can reproduce the bug without hiding the cause. Add or extend the connected E2E only when the defect is specifically about cross-boundary composition. Do not add production abstractions solely to shorten tests, and do not move unique safety/recovery behavior into one large E2E scenario.

## Coverage Gate

The normal deterministic suite measures line coverage for `src/qbet`. Coverage configuration lives in `pyproject.toml`; CI does not own a separate hidden threshold.

The required gate is:

- minimum total line coverage: **85%**;
- generated migrations under `src/qbet/storage/migrations/` and `src/qbet/web/migrations/` are omitted because they are generated schema history rather than application behavior;
- no application service, engine, web module, adapter, repository or safety boundary is excluded merely to reach the threshold;
- `coverage.xml` is generated and uploaded by CI for inspection;
- the terminal `coverage report` is the human-readable gate and reads the threshold from repository configuration.

Local standard coverage validation is:

```powershell
python -m pytest --cov=src/qbet --cov-report=xml:coverage.xml --cov-report= --durations=20
python -m coverage report
```

The explicit command keeps opt-in suites independent. The performance job and external bunq sandbox E2E do not inherit the 85% threshold and are not required to make the normal deterministic suite pass.

## CI Boundaries

- normal deterministic CI runs the full standard Pytest suite with repository-wide `src/qbet` line coverage;
- `bunq_e2e` stays explicit and opt-in;
- `performance` stays marker/environment gated and does not contribute to the required 85%;
- normal tests must not initiate real-money actions or uncontrolled provider writes.

## Fixture Decision

The audit deliberately does not create a new catch-all provider fixture module. Existing `tests/support/workflow.py` already owns the stable workflow request/handler helpers. The connected Odds, score, and bunq fakes remain close to the tests that define their HTTP/payment contracts because combining them would hide domain intent rather than reduce meaningful duplication.
