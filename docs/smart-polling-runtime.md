# Hosted Smart Polling Runtime

Q-Bet keeps Smart Polling policy in Python and uses Supabase only to wake one bounded Django request. The hosted path does not introduce Celery, Redis, browser automation, or a second scheduling policy.

## Runtime Flow

```text
Supabase Cron
    |
    v
POST /internal/polling/tick/
    |
    +-- Bearer token check
    +-- load global routing + user preferences
    +-- load persisted polling strategies
    +-- seed/reuse durable qbet_polling_work identities
    +-- claim at most QBET_POLLING_TICK_MAX_WORK due rows
    |
    v
SmartPollingPolicy
    |
    +-- future/fresh/capacity/disabled/terminal -> persist typed outcome, no provider call
    |
    +-- due -> TheOddsApiAdapter -> NormalizedMarketSnapshot
                          |
                          v
                PostgreSQL work state
                + Monitoring activity
```

The work identity includes owner, source, target, engine, mode, sport, event, and market. Repeated wake-ups therefore reuse the same durable row instead of creating duplicate due work. Processing rows carry a bounded lease so an interrupted Vercel request can be recovered on a later tick.

Simulation and Execution remain separate route identities. This hosted data-acquisition tick never places an order, moves money, approves Execution, or replaces the final Execution `RequestHandler` revalidation boundary.

## Application Configuration

Configure the Vercel/Django runtime with deployment secrets or environment variables:

- `QBET_POLLING_TICK_TOKEN`: high-entropy secret required as `Authorization: Bearer ...`.
- `QBET_POLLING_TICK_MAX_WORK`: bounded work count per HTTP wake-up, default `10`, accepted range `1..100`.
- `QBET_POLLING_ODDS_SPORT`: The Odds API sport key. Falls back to `QBET_SIMULATION_ODDS_SPORT`.
- `QBET_POLLING_ODDS_EVENT_ID`: exact event id. Falls back to `QBET_SIMULATION_ODDS_EVENT_ID`.
- `QBET_POLLING_ODDS_MARKET`: exact market key. Falls back to `QBET_SIMULATION_ODDS_MARKET`.
- `QBET_POLLING_ODDS_EVENT_STARTS_AT`: timezone-aware ISO-8601 event start, for example `2026-10-01T18:30:00Z`.
- `QBET_THE_ODDS_API_KEY`: existing provider credential boundary.

Do not store any of these secret values in GitHub, issue bodies, screenshots, Monitoring records, or the Supabase Cron command text.

The current hosted slice intentionally targets one configured event/market. This keeps the provider request explicit and testable while the runtime/queue remains capable of storing many independent work identities. Broader event discovery can seed additional durable targets later.

## Supabase Wake-Up

Supabase's current hosted Cron capability uses `pg_cron`; asynchronous HTTP wake-ups use `pg_net`. Supabase recommends Vault for authorization material. Q-Bet follows that split:

1. Enable Supabase Cron / `pg_cron` and `pg_net` for the deployment database.
2. Store the full protected endpoint URL as Vault secret `qbet_polling_tick_url`.
3. Store the same value as Vercel's `QBET_POLLING_TICK_TOKEN` in Vault as `qbet_polling_tick_token`.
4. Apply `supabase/operator/polling_cron.sql` once for that environment.
5. Inspect Cron run history and Q-Bet Monitoring after activation.

Example Vault setup, with real values entered only in the Supabase SQL editor or another approved secret-management path:

```sql
select vault.create_secret(
    'https://your-qbet-host.example/internal/polling/tick/',
    'qbet_polling_tick_url'
);

select vault.create_secret(
    '<same-secret-as-QBET_POLLING_TICK_TOKEN>',
    'qbet_polling_tick_token'
);
```

The checked-in Cron command resolves both values from Vault at runtime. It does not embed the URL token or provider API key in `cron.job`.

The default operator schedule wakes Q-Bet once per minute. That does **not** mean one provider request per minute: each wake-up re-evaluates persisted `SmartPollingPolicy` state, and fresh/future/disabled/capacity-limited work exits before provider I/O.

## Safe Activation And Rollback

This repository change does not mutate a production Supabase project. Activation is an explicit operator action because it changes a hosted environment.

Before activation:

- deploy the Django code and migration;
- set the Vercel polling token and target configuration;
- verify the protected endpoint returns 404 without the token;
- verify an authorized tick returns only aggregate counts;
- configure at least one enabled market polling strategy and an effective user/global route.

To stop wake-ups, unschedule the named `qbet-smart-polling-tick` Cron job through Supabase Cron. Existing `qbet_polling_work` rows and Monitoring history remain intact. Disabling the Q-Bet route or polling strategy also prevents new provider work while preserving prior durable state.

## Observability

Material runtime outcomes use the existing Monitoring boundary. The polling scheduler records scheduling, freshness/capacity deferral, disabled/terminal decisions, and provider query activity. Provider activity records contain safe references such as work id, provider/source id, and target, never credentials or raw provider payloads.

The HTTP response intentionally contains aggregate counts only:

```json
{
  "status": "ok",
  "eligible_routes": 1,
  "claimed": 1,
  "provider_calls": 0,
  "successes": 0,
  "deferred": 1,
  "failures": 0,
  "terminal": 0
}
```
