# Read-Only Bank Balance Adapter

`qbet.bank` is a provider-neutral, read-only boundary for observed bank balances. It accepts a
configured provider/source, an opaque redacted account reference in the documented
`opaque-label-***1234` format, required currency, correlation ID, and freshness threshold. The
label contains no account digits and the visible suffix is limited to four digits, so a raw bank
identifier cannot be carried through the domain models. It returns a normalized
`BankBalanceOutcome` before any future capital-coverage consumer can use the value.

An official bank adapter implements `BankBalanceProvider.read_balance(request)`. Provider
credentials, OAuth handling, account identifiers, transport clients, and raw response payloads
remain inside that adapter's infrastructure configuration. They do not belong in `BankBalance`,
requests, outcomes, logs, or persistence models.

The boundary contains no transfer, deposit, withdrawal, top-up, reservation, order, or ledger
operation. A future consumer may call `require_fresh_balance()` only after source, account,
correlation, currency, amount, and freshness validation has succeeded. Amounts accept `Decimal`
or lossless decimal text; binary Python floats are rejected at the boundary.

`DeterministicBankBalanceProvider` is the fixture-backed sandbox implementation for tests and
local development. It performs no network access and has no credential configuration.
