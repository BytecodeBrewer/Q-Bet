# Phase 3 Hosted Web Performance

This companion to [Phase 3 Performance Baseline](phase-3-performance-baseline.md) measures the hosted Django/Vercel/Supabase path. It is deliberately separate from the internal Python/PostgreSQL performance suite.

The figures below are observations from the named environment and date, not production SLAs.

## Scope and safety

The standard hosted check uses only credential-free public requests plus aggregate request/query telemetry. It does not require provider, bank, or user credentials and must not create production users or mutate shared operational data.

Authenticated Dashboard, Reporting, Monitoring, Settings, and Simulation measurements are collected only during an already-authorized operator session. The procedure records aggregate timings and query counts, never SQL text, parameters, cookies, account tokens, provider credentials, bank data, or raw payloads.

## Baseline environment

Observed on 25 September 2026:

- Vercel project: `q-bet`
- production alias: `q-bet.vercel.app`
- production release at measurement time: `1c483f93943e5c0778b3a10003de9fd7d845d554`
- current pre-ticket Vercel Function region: `iad1`
- Supabase project: `qbet`
- PostgreSQL: 17
- Supabase region: `eu-west-1`

The production alias was older than current `develop` during the baseline. Production and preview measurements therefore must always record the exact release SHA before comparisons are interpreted.

## Public hosted observations

Q-Bet middleware `request.completed.duration_ms` measures Django/application request duration. It is not a browser DNS/TLS/TTFB measurement.

Observed production landing page:

- idle/cold-ish request: 94.351 ms
- warm requests: 1.131–2.117 ms

Observed production login GET:

- first observed request: 9.258 ms
- warm requests: 2.798–3.099 ms

Observed current preview landing page before this ticket:

- first observed request: 124.163 ms
- later request: 1.485 ms

Observed production `/health/`, which exercises persistence/readiness:

- 905.396 ms
- 791.722 ms
- 791.351 ms
- 795.272 ms
- 803.959 ms

The persistence-heavy route remains around 0.8 seconds while warm public rendering is around 1–3 ms. The evidence does not support treating general Django template rendering or Vercel Free as the sole bottleneck.

## PostgreSQL evidence

At baseline the relevant live workload tables contained no execution, approval, notification, inbox, or monitoring workload rows. The measurements therefore establish request topology and query-shape costs, not production-scale capacity.

Supabase Performance Advisor reported no missing-index finding.

Representative `pg_stat_statements` mean execution times were small inside PostgreSQL:

- routing configuration read: about 0.238 ms
- `django_migrations` read: about 0.218 ms
- display preference read: about 0.069 ms

The large difference between database execution time and hosted persistence-heavy request time points to connection/network roundtrips and repeated database calls as material contributors.

## Material query-shape findings

### Approval navigation

Before this ticket, the navbar approval count loaded every `awaiting_approval` execution row, deserialized every full JSON payload, and filtered ownership in Python.

The optimized repository applies the owner predicate in PostgreSQL and projects only the JSON payload needed for the matching records.

### Notification navigation

Before this ticket, the navbar unread badge materialized up to 100 inbox deliveries, fetched matching notification tasks and read markers, constructed customer-safe inbox objects, and counted unread items in Python.

The optimized path performs a bounded database count with owner-task and read-marker existence checks and does not deserialize notification payloads for the badge.

### Display preferences

Reporting and Settings previously loaded durable display preferences explicitly and then loaded them again through the shared authenticated shell in the same request.

The optimized path caches the preference object only on the current `HttpRequest`. There is no cross-request cache and no freshness change.

## Vercel region

The baseline Vercel Function region was `iad1` while the authoritative Supabase PostgreSQL project is in `eu-west-1`.

This ticket configures:

```json
"regions": ["dub1"]
```

The region change must be validated on a preview deployment before any production promotion. The repository must not claim a latency improvement until a `dub1` preview is measured.

## Opt-in hosted query profiling

Set:

```text
QBET_PROFILE_WEB_REQUESTS=true
```

to add these aggregate fields to the existing `request.completed` structured log:

- `query_count`
- `query_duration_ms`

When the flag is disabled, the existing request-log contract remains unchanged.

The profiler uses Django's database execution wrapper for the current request only. It never logs SQL text, parameters, database payloads, credentials, cookies, or account tokens.

## Repeatable procedure

1. Record the exact deployment SHA and Vercel Function region.
2. Confirm the Supabase project/region without copying connection secrets.
3. Measure public `/` and `/accounts/login/` after an idle period, then repeat several warm requests.
4. Measure `/health/` separately because it deliberately exercises persistence/readiness.
5. Enable `QBET_PROFILE_WEB_REQUESTS=true` only in the intended preview/operator environment.
6. During an already-authorized operator session, visit:
   - Dashboard
   - Reporting
   - Monitoring/admin surface where applicable
   - Settings
   - one Simulation page/action that requires no real provider credentials
7. Record `duration_ms`, `query_count`, and `query_duration_ms` from the safe structured request logs.
8. Inspect browser network/DOM timing where an approved browser environment is available and record static request count/payload size.
9. Disable the profiling flag after the sample.
10. Compare the exact before/after deployment SHAs and document any optimization with both measurements.

## Browser/static baseline

The current `develop` global CSS/JS source footprint inspected during this ticket was about 70 KB before compression. Existing static responses were served through Vercel caching. No measurement showed static asset size as the dominant delay.

A controlled browser DOM-ready/interaction sample was not available in the agent execution environment, so no synthetic browser timing is reported as fact.

## After-change preview observations

After the bounded changes above, the exact PR head `0c0e0d9ec392c37a056e0493ebbe47377bfc121a` deployed successfully to Vercel in `dub1`.

Comparable persistence-heavy preview observations:

Pre-ticket `iad1` preview `/health/` samples (status 503):
- 975.612 ms
- 747.839 ms
- median: about 862 ms

Post-change `dub1` preview first/idle-ish `/health/` samples across equivalent runtime-code deployments (status 503):
- 248.997 ms
- 201.663 ms
- 179.193 ms
- 160.484 ms
- 200.649 ms
- 158.463 ms
- median: about 190 ms

Post-change warm repeated `/health/` samples (status 503):
- 42.087 ms
- 32.164 ms
- 43.150 ms
- 31.212 ms
- median: about 37 ms

In this small sample, the median first/idle-ish persistence-heavy request fell by about 78% (roughly 4.5x faster) and the warm median fell by about 96% (more than 20x faster) relative to the two pre-ticket `iad1` preview observations.

These numbers are directional hosted observations only. They are not latency SLAs, and the 503 readiness status remains a separate degraded-state signal that must not be presented as healthy merely because it returns faster.

Public rendering on the exact PR-head preview remained fast: one observed landing request completed its Django/application work in 29.699 ms after deployment. Preview protection limited repeated public/login fetches through the agent connector, so no synthetic browser or authenticated page timing is invented.

## Remaining constraints

- Cold-start behavior is observable but variable and should not be converted into a strict SLA from a handful of requests.
- Current operational tables are small, so future row growth requires repeating query-shape and index analysis.
- Authenticated hosted timings require a normal authorized session; the standard performance procedure intentionally does not create or store credentials.
- Provider/browser execution latency, real-money load testing, destructive stress testing, and Phase 4 browser-agent infrastructure remain out of scope.
