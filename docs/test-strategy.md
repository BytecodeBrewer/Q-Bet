# Q-Bet Test Strategy And Regression Ownership

## Purpose

The test suite should make failures local and explain which architectural boundary regressed. A larger end-to-end test proves that components compose; it does not replace focused tests for calculations, recovery, idempotency, authorization, provider errors, or capital safety.

Normal CI is offline and credential-free. Real external sandbox or read-only verification remains explicitly opt-in.

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
- `tests/integration/web/test_phase2_completion_boundaries.py` becomes `tests/integration/web/test_execution_approval_ui.py`; the surviving scenarios protect durable GUI Simulation recovery plus owner/authorization/rejection approval behavior rather than a historical phase.
- `tests/integration/web/test_issue_112_review_regressions.py` becomes `tests/integration/web/test_durable_simulation_and_dispatch.py`; its surviving regressions protect shared Simulation ledger merging and persisted-owner dispatch behavior rather than one historical issue number.

Git history retains the original issue/phase context.


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
| Phase 2 completion: GUI Simulation ledger recovery | keep and rename | `test_execution_approval_ui.py` currently retains the GUI persistence/recovery regression |
| Phase 2 completion: owner-only business approval UI | keep and rename | `test_execution_approval_ui.py` |
| Phase 2 completion: rejected/unauthorized decision safety | keep and rename | `test_execution_approval_ui.py` |
| Issue 112: GUI Simulation preserves shared ledger history | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: stale Simulation snapshots merge safely | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: persisted owner used by global due claim | keep and rename | `test_durable_simulation_and_dispatch.py` |
| Issue 112: routed Simulation durable merge | keep and rename | `test_durable_simulation_and_dispatch.py` |

The remaining `tests/integration/web/test_phase2_visuals.py` name is intentionally not changed in this ticket while #159 owns overlapping Monitoring/operability UI surfaces. That avoids a parallel rename/edit conflict; it is not part of the four legacy gates audited above.

## Adding Future Regressions

Place a regression at the narrowest layer that can reproduce the bug without hiding the cause. Add or extend the connected E2E only when the defect is specifically about cross-boundary composition. Do not add production abstractions solely to shorten tests, and do not move unique safety/recovery behavior into one large E2E scenario.

## CI Boundaries

- normal `python -m pytest` remains offline and credential-free;
- `bunq_e2e` stays explicit and opt-in;
- `performance` stays marker/environment gated;
- normal tests must not initiate real-money actions or uncontrolled provider writes.

## Fixture Decision

The audit deliberately does not create a new catch-all provider fixture module. Existing `tests/support/workflow.py` already owns the stable workflow request/handler helpers. The connected Odds, score, and bunq fakes remain close to the tests that define their HTTP/payment contracts because combining them would hide domain intent rather than reduce meaningful duplication.
