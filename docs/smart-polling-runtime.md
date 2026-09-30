# Smart Polling Hosted Runtime

This document describes the Phase 3 hosted wake-up contract implemented by issue #207.

## Runtime boundary

The hosted runtime is intentionally split into two responsibilities:

```text
Supabase Cron
  -> POST /internal/polling/tick/
  -> durable route/work synchronization
  -> bounded due-work claim
  -> SmartPollingPolicy
  -> The Odds API only when due
  -> normalized snapshot + Monitoring + next durable state
```

Supabase only wakes Q-Bet. Scheduling, freshness, capacity, retries, disabled/terminal state, and provider dispatch remain Python/domain decisions.

The tick is bounded by `QBET_POLLING_TICK_MAX_WORK` and each claimed item receives a short lease controlled by `QBET_POLLING_CLAIM_SECONDS`. This prevents one Vercel request from becoming an unbounded worker loop and prevents concurrent wake-ups from dispatching the same durable item at the same time.

## Required Q-Bet configuration

The endpoint is disabled unless `QBET_POLLING_TICK_TOKEN` is configured. The scheduler must send:

```text
Authorization: Bearer <QBET_POLLING_TICK_TOKEN>
```

Missing or incorrect authorization receives the same generic not-found response and the token is never rendered.

The current connected Phase 3 polling target is SportsCapital Simulation through The Odds API. It also requires:

- `QBET_SIMULATION_SPORTS_SOURCE=the_odds_api`
- `QBET_SIMULATION_ODDS_SPORT`
- `QBET_SIMULATION_ODDS_EVENT_ID`
- `QBET_SIMULATION_ODDS_MARKET`
- `QBET_SIMULATION_ODDS_EVENT_STARTS_AT` as a timezone-aware ISO-8601 timestamp
- `QBET_SIMULATION_ASSUMED_LIQUIDITY`
- the existing `QBET_THE_ODDS_API_KEY` deployment secret

The explicit event start is decision-relevant scheduling data. Missing or invalid timing fails closed instead of inventing a polling schedule.

Persisted user intent inside the global SportsCapital Simulation guardrail controls whether the shared provider market target is eligible to exist. Multiple eligible users do not multiply identical quotation requests. Removing all eligibility disables future work but keeps durable history and the latest normalized snapshot.

## Supabase Cron wake-up

Supabase's current hosted Cron capability is backed by `pg_cron`. Its documented `pg_net` integration can send asynchronous HTTP POST requests, and Supabase recommends Vault for scheduler authorization material.

The connected Supabase project is prepared for this runtime: Vault stores the protected tick URL/token and the required `pg_cron` / `pg_net` extensions are enabled. Configure the same `QBET_POLLING_TICK_TOKEN` directly in the Vercel Preview and Production environments. GitHub Actions does not query Supabase or transfer database secrets during deployment.

The recurring job must only be active when the public stable Q-Bet deployment actually contains the polling endpoint. Preview deployments are protected by Vercel Authentication and therefore are not valid scheduler targets. Activation is intentionally tied to the deployed stable release rather than to a protected preview URL.

Store the endpoint URL and bearer token in Supabase Vault using the Dashboard or another approved secret-management workflow. Suggested Vault names:

```text
qbet_polling_tick_url
qbet_polling_tick_token
```

The one-minute wake-up job uses only Vault lookups in its SQL command, so neither URL nor bearer token is embedded in the job definition:

```sql
select cron.schedule(
    'qbet-smart-polling-wake',
    '* * * * *',
    $$
    select net.http_post(
        url := (
            select decrypted_secret
            from vault.decrypted_secrets
            where name = 'qbet_polling_tick_url'
        ),
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer ' || (
                select decrypted_secret
                from vault.decrypted_secrets
                where name = 'qbet_polling_tick_token'
            )
        ),
        body := jsonb_build_object('source', 'supabase-cron'),
        timeout_milliseconds := 10000
    ) as request_id;
    $$
);
```

A one-minute wake cadence is deliberately more frequent than many configured provider refresh points. That does **not** mean one provider request per minute: `SmartPollingPolicy` skips fresh work and schedules future due points, while the durable queue only claims work whose `next_due_at` is due.

## Safe verification

Before enabling the recurring schedule on the stable public alias:

1. deploy the accepted release and apply its migrations;
2. verify `/health/` reports that exact release with persistence ready;
3. verify `QBET_POLLING_TICK_TOKEN` is configured in the Vercel environment;
4. POST the protected polling endpoint and verify a bounded JSON response;
5. confirm `qbet_polling_work` and Monitoring records change as expected;
6. activate `qbet-smart-polling-wake` only after that exact stable endpoint succeeds;
7. inspect Supabase Cron job history and Q-Bet Monitoring for failures without exposing secret values.

The scheduler must remain unscheduled while the stable alias still serves a release without the polling endpoint. This avoids intentionally generating repeated 404 traffic during review.

Normal CI never receives provider credentials and does not make live The Odds API calls. Integration tests inject deterministic collectors and use PostgreSQL state.

## Staff strategy presets

The staff Smart Polling settings surface resolves effective engine configuration with the same
`PollingStrategyResolver` used by runtime policy. A provider/target Default applies only when an
engine-specific Override is absent.

The three Phase 3 presets are deterministic form helpers. They populate explicit typed values and
do not create a hidden runtime mode:

| Preset | Freshness | Market refresh points | Latest market boundary | Result retry | Max attempts |
| --- | ---: | --- | ---: | ---: | ---: |
| Conservative | 15 min | T-24h, T-2h, T-15m | T-5m | 20 min | 2 |
| Standard | 5 min | T-24h, T-12h, T-2h, T-15m | T-1m | 10 min | 3 |
| Frequent | 2 min | T-24h, T-12h, T-2h, T-30m, T-10m | T-1m | 5 min | 5 |

Applying a preset changes timing and attempt values only. Provider capacity class, quota units,
request-cost units, enabled state, source identity, target, and engine/default identity remain
explicit settings. The operator can preview which current v1 engine routes would resolve
differently before saving. Preview never mutates PostgreSQL state and never performs a provider
request. Save is accepted only for the exact validated strategy and persisted strategy snapshot
covered by the server-signed preview token; changed values or changed persisted configuration
require a fresh preview.

## Separation from RequestHandler

Ordinary Smart Polling is Data Aggregation work. It must not become an alternative implementation of last-mile Execution revalidation.

Live Execution continues to use the existing `RequestHandler` targeted revalidation boundary. Simulation polling does not place bets, approve execution, move capital, reconcile manual Execution timeouts, or send execution notifications.

Manual Execution timeout reconciliation has its own protected hosted boundary at `POST /internal/execution/tick/`. It uses `QBET_EXECUTION_TICK_TOKEN` for scheduler authentication and `QBET_EXECUTION_TICK_MAX_WORK` to bound each wake-up. That endpoint only reconciles already-expired manual Execution actions and their authoritative reservation release; it does not perform provider execution or quotation polling.
