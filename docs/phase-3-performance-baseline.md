# Phase 3 Performance Baseline

Q-Bet Phase 3 starts measuring connected internal performance before latency-sensitive external integrations are added.

The baseline suite lives in `tests/performance/` and is intentionally separate from normal correctness tests. It measures representative product boundaries rather than trivial helper functions or typing abstractions.

## What is measured

The initial baseline covers:

- workflow scheduling, routing, persistence, and deterministic dispatch;
- PostgreSQL work-queue persistence and claiming;
- Monitoring append plus bounded query/reconstruction;
- administrator Monitoring JSON projection/export;
- a representative `BonusEngine` evaluation batch.

Workload sizes are fixed in the test so consecutive runs remain comparable.

## What is not measured

The baseline records internal Python/PostgreSQL/web-projection time only. It deliberately excludes:

- provider and general network latency;
- The Odds API, bunq, bookmaker, exchange, prediction-market, or crypto venue response time;
- browser automation;
- production-scale stress, soak, or capacity testing.

Those paths should receive their own measurements when their Phase 3 adapters exist. In particular, future exchange/WebSocket and crypto paths may require dedicated low-latency benchmarks without changing the existing engine contracts.

## Running the suite

Use an isolated PostgreSQL database. Never point the performance suite at the shared production Supabase database.

```powershell
$env:QBET_TEST_DATABASE_URL='postgresql://qbet:qbet@127.0.0.1:5432/qbet_test'
$env:QBET_DATABASE_URL=$env:QBET_TEST_DATABASE_URL
$env:QBET_RUN_PERFORMANCE='1'
python manage.py migrate --noinput
python -m pytest -m performance tests/performance -s
```

Set `QBET_PERFORMANCE_OUTPUT` to choose the JSON artifact path. The default is:

```text
artifacts/phase3-performance-baseline.json
```

## Result format

The JSON artifact contains a schema version, runtime scope, whether external-provider latency is included, and one entry per measured boundary with:

- workload size;
- wall-clock duration in milliseconds;
- derived items per second.

These values are comparative baselines, not production SLAs. The initial suite intentionally has no fragile microsecond-level regression threshold. A later ticket may add broad regression budgets after enough comparable CI runs exist to establish normal variance.
