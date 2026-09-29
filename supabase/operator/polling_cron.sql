-- Q-Bet Phase 3 Smart Polling wake-up.
--
-- OPERATOR-APPLIED ONLY. This file is intentionally not a Django/Supabase migration.
-- Before running it, create Vault secrets named:
--   qbet_polling_tick_url
--   qbet_polling_tick_token
-- Never replace those lookups with plaintext credentials in this file.

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net;

select cron.schedule(
    'qbet-smart-polling-tick',
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
        body := '{}'::jsonb,
        timeout_milliseconds := 10000
    ) as request_id;
    $$
);
