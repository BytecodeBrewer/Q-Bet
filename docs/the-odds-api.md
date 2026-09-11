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

The adapter uses The Odds API v4's event-odds endpoint with decimal odds and
ISO timestamps. It requires a matching event/sport identity, the requested
market for each returned bookmaker, at least two outcomes, and a market
timestamp. Unsafe payloads are rejected before they enter Q-Bet's preparation
pipeline. The provider documents API-key query authentication, regions,
markets, oddsFormat, dateFormat, response update timestamps, usage headers,
and rate-limit responses in its v4 API guide:
https://the-odds-api.com/liveapi/guides/v4/
