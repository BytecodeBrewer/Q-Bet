# Simulation And Execution E2E Scenario Matrix

Issue #99 verifies the deterministic, PostgreSQL-backed boundaries already present in Q-Bet. All scenarios use typed fixture data and sandbox execution only; no provider, browser, bank, or live-money integration is involved.

| Scenario | Engine | Routing | Verified outcome |
| --- | --- | --- | --- |
| Simulation only | BonusEngine | Simulation | Report, ordered records, and an isolated Simulation PortfolioLedger lifecycle persist; no execution record is written. |
| Execution only | SportsCapitalEngine | Execution | Approval, reserve, lock, pending, sandbox dispatch, settlement, and retrieval persist in the execution context. |
| Single-mode dispatch | BonusEngine | Simulation | One opportunity is scheduled once and produces one persisted mode work item, history, report, and Simulation ledger context. |
| Dual mode | SportsCapitalEngine | Simulation + Execution | One opportunity fans out once into two persisted mode work items with independent histories, ledgers, report/record state, and capital contexts. |
| Recheck and expiry | SportsCapitalEngine | Execution / Simulation | A changed revalidation enters a durable waiting `recheck` state; an expired item is never dispatched and is stored as expired. |
| Repeated delivery | SportsCapitalEngine | Execution | Repeating an already accepted result does not change lifecycle or ledger state. |
| Cancellation | BonusEngine | Simulation + Execution | A cancelled execution releases its own pending principal without changing the completed simulation report. |
| Revalidation rejection | SportsCapitalEngine | Execution | Mode-specific RequestHandler rejection cancels the routed queue item before execution state, capital reservation, or settlement exists. |

## Current Boundary

The `ModeDispatchCoordinator` is the deterministic scheduling boundary for one eligible opportunity. It writes a distinct PostgreSQL queue item and append-only in-payload history for every enabled mode; the same stable work ID cannot create a duplicate queue record. Simulation has its own virtual-capital report model, PostgreSQL report history, and a mode-specific PortfolioLedger lifecycle for evaluated steps. Execution keeps its separate approval and settlement lifecycle snapshot. The matrix verifies that these persisted queue, history, capital, and result contexts do not mix.
