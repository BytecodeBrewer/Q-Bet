# The Odds API Development Smoke Path

The first Phase 3 provider connection is read-only. It retrieves one selected
event market through TheOddsApiAdapter, then applies the existing normalized
snapshot readiness checks and Sports Match Builder unchanged.

Set the credential only in the process environment:

    $env:QBET_THE_ODDS_API_KEY='your-development-key'

Construct a DataCollectionRequest with source.provider_id set to
the_odds_api, source.transport set to api, and explicit sport, event_id, and
market values. Calling TheOddsApiAdapter().fetch(request) performs the
optional live read. Do not use this in normal test runs or CI: each provider
request can consume the account quota.

The adapter uses The Odds API v4's event-odds endpoint with decimal odds. It
requires a matching event/sport identity, the requested market for each
returned bookmaker, and at least two outcomes. Q-Bet records its own response
receipt time as `fetched_at` and does not transport provider `last_update`
timestamps into normalized offers or snapshots. Unsafe payloads are rejected
before they enter Q-Bet's preparation pipeline. The provider documents API-key
query authentication, regions, markets, oddsFormat, dateFormat, usage headers,
and rate-limit responses in its v4 API guide:
https://the-odds-api.com/liveapi/guides/v4/

## Connected SportsCapital Simulation

GUI-started SportsCapital Simulation can opt into one configured two-outcome The Odds API event/market. The connected path remains read-only and follows:

    TheOddsApiAdapter
      -> NormalizedMarketSnapshot
      -> deterministic best-offer selection
      -> Sports Match Builder
      -> SportsCapitalEngineRequest
      -> existing Simulation workflow

Fixture mode remains the default so local development and normal CI never consume provider quota.

Set `QBET_SIMULATION_SPORTS_SOURCE=the_odds_api` to enable the connected source. The following values are then required:

- `QBET_SIMULATION_ODDS_SPORT` — provider sport key.
- `QBET_SIMULATION_ODDS_EVENT_ID` — exact event identifier.
- `QBET_SIMULATION_ODDS_MARKET` — exact market key. The first connected path accepts only snapshots with exactly two distinct outcomes.
- `QBET_SIMULATION_ASSUMED_LIQUIDITY` — Simulation-only assumed available stake. The Odds API does not provide this value.
- `QBET_SIMULATION_REQUESTED_TOTAL_STAKE` — total virtual stake requested by the two-way arbitrage builder.
- `QBET_SIMULATION_STAKE_PRECISION` — virtual stake precision used for both selected outcomes.

The API credential remains `QBET_THE_ODDS_API_KEY` and must stay in the environment/deployment secret boundary. Connected Simulation never logs the key, a credential-bearing URL, or raw provider payload. Provider failures are mapped to stable Simulation reason codes and normal tests inject offline collectors/transports.


### Connected BonusEngine financial/risk inputs

BonusEngine Simulation uses the same configured event/market source, but promotion metadata remains user-owned and separate from market quotations. A connected BonusEngine run also requires:

- persisted provider/account risk state for the selected canonical promotion sportsbook;
- explicit provider-specific financial terms through `QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS`.

The financial-terms value is a JSON provider mapping keyed by canonical sportsbook id. Each entry declares `fee_rate` as a decimal fraction and `tax_mode` as `none`, `stake`, or `profit`. The German `stake` / `profit` tax modes use the project-standard 5.3 percent rate.

Example:

    $env:QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS='{"tipico":{"fee_rate":"0","tax_mode":"none"},"winamax":{"fee_rate":"0.01","tax_mode":"profit"}}'

Missing or invalid provider state and missing fee/tax terms fail closed. Q-Bet does not assume zero fees, zero tax, or a healthy sportsbook account when those decision-critical inputs are unavailable.


## Post-Event Scores And Settlement

The Odds API v4 scores endpoint is the first connected post-event result source. Result collection remains separate from pre-execution RequestHandler revalidation:

    trackable ACKNOWLEDGED Execution
      -> ResultCollectionRequest + ResultProviderTarget
      -> TheOddsApiScoreCollector
      -> ResultCollectionOutcome
      -> SettlementService
      -> authoritative ExecutionRecord / Portfolio Ledger

The collector performs one read-only request for an explicit sport and provider event id using the provider's `eventIds` filter. `daysFrom` is bounded to the provider-supported 1-3 day range and ISO timestamps are required.

A completed provider event contributes typed score/finality evidence only. It does not infer whether the Q-Bet execution was financially successful. Existing execution/sandbox payout state remains the financial settlement input; the score result proves post-event finality and is persisted separately on the authoritative ExecutionRecord.

Live/incomplete events return partial or not-yet-available states without mutating the ledger. Missing events remain retryable/non-final rather than being fabricated as cancellations. Identity mismatches and malformed payloads fail closed.

The same `QBET_THE_ODDS_API_KEY` environment/deployment secret boundary is used for scores. Normal tests inject HTTP and remain offline; credentials, credential-bearing URLs and raw provider payloads must never be stored or surfaced.
