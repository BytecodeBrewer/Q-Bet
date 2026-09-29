# Bank Funding Proposal Sandbox

`qbet.bank` exposes an approval-gated proposal boundary for future funding and withdrawal
workflows. `BankFundingProposal` contains only a direction, role-based source and destination,
Decimal amount, currency, purpose, target capital context, correlation ID, expiry, and lifecycle
timestamps. It never includes credentials, account numbers, raw bank payloads, or transport
configuration.

`BankFundingProposalService` is pure. It validates a proposed amount against a fresh normalized
`BankBalance` and the matching `PortfolioLedger` context, then records an explicit authenticated
approval in an immutable returned proposal. It does not call a provider and does not mutate the
ledger or any bank state.

`DeterministicFundingSandboxAdapter` accepts only an approved proposal and returns an idempotent
acknowledgement for tests and local development. It performs no network access and cannot send a
transfer, top-up, withdrawal, or other money movement. A future live adapter must remain behind a
separate, explicitly approved integration boundary.


## Capital Movement Lifecycle

Requirement-backed capital work is now explicit:

```text
CapitalRequirement
-> funding_attention
-> BankFundingProposal
-> authenticated approval
-> manual or supported sandbox action
-> Pending
-> reconciliation evidence
-> PortfolioLedger
```

A proposal is not a completed movement, and provider acknowledgement is not reconciliation.
Manual completion and bunq sandbox acknowledgement create durable `Pending`
`CapitalMovementRecord` state. The authoritative `PortfolioLedger` changes only after a later
`CapitalMovementObservation` confirms the exact amount, currency, source and destination and
records the reconciliation evidence source. Failed, cancelled, expired, or mismatched observations
remain visible without fabricating capital.

Requirement-backed proposals also record when their `funding_attention` notice was created.
Approval fails closed if that attention step is missing or occurs after the attempted approval.
