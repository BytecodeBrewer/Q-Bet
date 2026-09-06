# Simulation And Execution E2E Scenario Matrix

Issue #99 verifies the deterministic, PostgreSQL-backed boundaries already present in Q-Bet. All scenarios use typed fixture data and sandbox execution only; no provider, browser, bank, or live-money integration is involved.

| Scenario | Engine | Routing | Verified outcome |
| --- | --- | --- | --- |
| Simulation only | BonusEngine | Simulation | Report, ordered records, and an isolated Simulation PortfolioLedger lifecycle persist; no execution record is written. |
| Execution only | SportsCapitalEngine | Execution | Approval, reserve, lock, pending, sandbox dispatch, settlement, and retrieval persist in the execution context. |
| Dual mode | SportsCapitalEngine | Simulation + Execution | Routing creates distinct work IDs and capital contexts; simulation report and execution record remain separate durable records. |
| Repeated delivery | SportsCapitalEngine | Execution | Repeating an already accepted result does not change lifecycle or ledger state. |
| Cancellation | BonusEngine | Simulation + Execution | A cancelled execution releases its own pending principal without changing the completed simulation report. |
| Revalidation rejection | SportsCapitalEngine | Execution | Mode-specific RequestHandler rejection stops before execution state, capital reservation, or settlement exists. |

## Current Boundary

Simulation has its own virtual-capital report model, PostgreSQL report history, and a mode-specific PortfolioLedger lifecycle for evaluated steps. Execution keeps its separate approval and settlement lifecycle snapshot. The matrix verifies that these persisted capital and result contexts do not mix.
