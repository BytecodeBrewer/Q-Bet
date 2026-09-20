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

- `tests/e2e/test_phase2_operability_gate.py` is removed. Its composition assertions are owned by the connected Phase 3 E2E; routing settings, approval behavior, revalidation, Monitoring export and access control remain covered by their focused integration suites.
- `tests/e2e/test_mode_settlement_isolation.py` is no longer treated as an E2E gate. Its unique queue/claim/expiry/async/result-state/isolation cases move to `tests/integration/workflow/test_mode_queue_and_isolation.py`. Duplicated single/dual-mode happy paths, immediate settlement happy paths and repeated-settlement checks are removed because current E2E/settlement tests own them.
- `tests/integration/web/test_phase2_completion_boundaries.py` becomes `tests/integration/web/test_execution_approval_ui.py`; the surviving scenarios protect durable GUI Simulation recovery plus owner/authorization/rejection approval behavior rather than a historical phase.
- `tests/integration/web/test_issue_112_review_regressions.py` becomes `tests/integration/web/test_durable_simulation_and_dispatch.py`; its surviving regressions protect shared Simulation ledger merging and persisted-owner dispatch behavior rather than one historical issue number.

Git history retains the original issue/phase context.

## Adding Future Regressions

Place a regression at the narrowest layer that can reproduce the bug without hiding the cause. Add or extend the connected E2E only when the defect is specifically about cross-boundary composition. Do not add production abstractions solely to shorten tests, and do not move unique safety/recovery behavior into one large E2E scenario.

## CI Boundaries

- normal `python -m pytest` remains offline and credential-free;
- `bunq_e2e` stays explicit and opt-in;
- `performance` stays marker/environment gated;
- normal tests must not initiate real-money actions or uncontrolled provider writes.
