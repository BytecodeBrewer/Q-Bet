# bunq Adapter

Q-Bet connects bunq through the existing provider-neutral bank boundaries. The adapter is intentionally split into a read-only production-capable mode and an explicit official-sandbox mode.

## Modes

`read_only` is the default. It uses the bunq production API environment for account/balance reads and technically rejects every provider write through the Q-Bet bunq adapter.

`sandbox` uses bunq's official sandbox API environment. A fake-money payment is permitted only when all of these conditions are true:

- the adapter is explicitly configured with `QBET_BUNQ_MODE=sandbox`;
- the caller provides an already approved `BankFundingProposal`;
- the proposal targets the `execution` capital context;
- the proposal direction is funding;
- a sandbox recipient is explicitly configured.

Simulation never triggers a bunq payment. The adapter does not mutate `PortfolioLedger`; it returns a correlated provider outcome for the existing settlement/monitoring boundaries.

## SDK Boundary

The integration uses the official bunq Python SDK through a lazy transport boundary. The optional dependency is pinned to `bunq_sdk==1.28.0` and is not installed by normal Q-Bet runtime/CI dependencies.

Install it only for bunq integration work:

```powershell
python -m pip install '.[bunq]' -r requirements-dev.txt
```

Keeping the SDK optional prevents its legacy dependency set from becoming a requirement for the rest of Q-Bet and keeps the normal test suite fully offline.

## Configuration

Never commit or print credential values.

Required when a bunq adapter is instantiated:

- `API_KEY_BUNQ` — bunq API key supplied through a secret/environment boundary.
- `QBET_BUNQ_ACCOUNT_REFERENCE` — Q-Bet-only opaque label in the form `opaque-label-***1234`; never use an IBAN or raw provider account identifier.
- `QBET_BUNQ_MODE` — `read_only` or `sandbox`; omitted values default to `read_only`.

Optional:

- `QBET_BUNQ_DEVICE_DESCRIPTION` — device description registered with bunq.
- `QBET_BUNQ_SANDBOX_RECIPIENT_EMAIL` — required only for sandbox payment E2E execution.

The adapter keeps the API key only in runtime memory. It does not persist an SDK context file and never returns raw bunq account/payment IDs. Payment IDs are reduced to a stable hashed Q-Bet provider reference.

## Offline Tests

Normal `pytest` runs use fake transports. They cover:

- successful balance normalization;
- currency mismatch and stale balance rejection;
- unavailable/invalid provider responses;
- authentication, timeout, and rate-limit reason codes;
- write blocking in read-only mode;
- approval and Execution-context guards;
- idempotent sandbox payment attempts.

No `API_KEY_BUNQ` means normal CI performs no bunq network request.

## Protected Sandbox E2E

`.github/workflows/bunq-sandbox-e2e.yml` is manual-only and uses the GitHub Environment `bunq-sandbox`.

Configure that environment with:

- secret `API_KEY_BUNQ` containing a sandbox API key;
- variable `QBET_BUNQ_ACCOUNT_REFERENCE` containing only the redacted Q-Bet account label;
- secret `QBET_BUNQ_SANDBOX_RECIPIENT_EMAIL` containing the sandbox recipient alias.

The workflow forces `QBET_BUNQ_MODE=sandbox` and `QBET_BUNQ_E2E=1`. The E2E test reads the sandbox balance and sends exactly EUR 0.01 through the approved Q-Bet funding boundary. It refuses to run in read-only mode and skips the payment when the configured fake-money account has insufficient sandbox balance.

A production/personal bunq API key must never be placed in the `bunq-sandbox` environment. Production integration remains read-only in this Phase 3 ticket.
