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

Only persisted user intent inside the global SportsCapital Simulation guardrail creates active polling work. Removing that eligibility disables future work but keeps its durable history and latest normalized snapshot.

## Supabase Cron wake-up

Supabase's current hosted Cron capability is backed by `pg_cron`. Its documented `pg_net` integration can send asynchronous HTTP POST requests, and Supabase recommends Vault for scheduler authorization material.

For this ticket, production scheduler mutation is intentionally **not** performed automatically. The repository ships the protected endpoint and this operator recipe; applying it to the connected Supabase project requires separate environment authorization.

Store the endpoint URL and bearer token in Supabase Vault using the Dashboard or another approved secret-management workflow. Suggested Vault names:

```text
qbet_polling_tick_url
qbet_polling_tick_token
```

After `pg_cron` and `pg_net` are enabled for the project, an operator can create a one-minute wake-up job without embedding either value in the cron command:

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

Before enabling a production schedule:

1. deploy the branch/accepted change and migrations;
2. configure the Vercel deployment secret `QBET_POLLING_TICK_TOKEN`;
3. configure the matching scheduler token in Supabase Vault;
4. POST the endpoint manually with the correct token and verify a bounded JSON response;
5. confirm `qbet_polling_work` and Monitoring records change as expected;
6. create the Cron wake-up only after the protected endpoint has been verified;
7. inspect Supabase Cron job history and Q-Bet Monitoring for failures without exposing secret values.

Normal CI never receives provider credentials and does not make live The Odds API calls. Integration tests inject deterministic collectors and use PostgreSQL state.

## Separation from RequestHandler

Ordinary Smart Polling is Data Aggregation work. It must not become an alternative implementation of last-mile Execution revalidation.

Live Execution continues to use the existing `RequestHandler` targeted revalidation boundary. Simulation polling does not place bets, approve execution, move capital, or send execution notifications.
