# RequestHandler Contract

`RequestHandler` is a targeted refresh and result-retrieval seam. It is not a
market-data ingestion path and it does not calculate stakes, reserve capital,
dispatch orders, settle results, or mutate a ledger.

## Mode Isolation

Simulation and controlled Execution use separate handler contracts and separate
handler instances. A `ModeRequest` contains only an opportunity identifier,
mode, correlation identifier, and lifecycle identifier. It deliberately carries
no credentials, provider session, bank, queue, or capital state.

## Revalidation

Before `DISPATCH`, `WorkflowOrchestrator` calls the handler for the current
mode. `valid` permits the normal dispatch stage. `changed` and `unavailable`
produce a recheck. `expired` and `rejected` block dispatch. Every non-valid
outcome requires a stable reason code.

## Result Retrieval

After a successful sandbox dispatch stage, the same mode handler returns a
typed result. It preserves the opportunity, mode, correlation, and lifecycle
identifiers. The result can be `success`, `failed`, `cancelled`, `partial`,
`unknown`, or `not_yet_available`; later reporting and settlement boundaries
consume it without allowing the handler to mutate capital.

The current implementations are deterministic fixtures only. Future permitted
API, Playwright, or result-data adapters implement the same mode-specific
contracts and must remain behind this boundary.
