# Phase 2 Operability and Observability Readiness

Issue #105 is the final technical gate before Phase 3 external adapters begin. The gate proves the accepted deterministic Phase 2 product path as one connected system, establishes a repeatable performance baseline, and exposes a minimal Prometheus-compatible telemetry boundary.

This document does **not** replace Q-Bet Monitoring. PostgreSQL-backed `MonitoringRecord` data remains the durable technical event, audit, and reconstruction source. The Prometheus surface is a non-authoritative operational projection intended for development and controlled deployments.

## Connected operability gate

The final E2E gate exercises the accepted Phase 2 boundaries together:

- administrator GUI routing configuration remains separate from user-originated work;
- inactive, Simulation-only, Execution-only, and dual-mode routing remain deterministic;
- Simulation and Execution create isolated work items and capital state;
- Execution cannot dispatch before explicit per-operation user approval;
- a recreated coordinator revalidates approved work before deterministic sandbox dispatch;
- replay does not duplicate queue work, execution lifecycle, ledger commands, or settlement;
- rejected final revalidation produces safe user state and persisted administrator diagnostics;
- normal-user surfaces do not expose technical Monitoring internals;
- staff can reconstruct the correlated path through bounded Monitoring export.

All connected tests use the isolated PostgreSQL test database configured through `QBET_TEST_DATABASE_URL`. They must never target the shared Supabase database.

## Performance baseline

Performance checks are separated from ordinary correctness tests with the `performance` pytest marker.

Run them locally against an isolated PostgreSQL database with:

```powershell
$env:QBET_DATABASE_URL=$env:QBET_TEST_DATABASE_URL
$env:QBET_PERFORMANCE_ARTIFACT='artifacts/phase2-performance.json'
python -m pytest -m performance -s
```

The Phase 2 baseline uses deterministic workload sizes and measures representative paths rather than trivial function calls:

| Measurement | Deterministic workload | Initial regression budget |
| --- | --- | ---: |
| Workflow/routing scheduling | 20 opportunities with dual-mode fan-out | 5000 ms |
| PostgreSQL queue claim | 40 durable mode work items | 5000 ms |
| Monitoring append + bounded query | 100 persisted Monitoring records | 5000 ms |
| Dashboard projection | one authenticated administrator dashboard request | 5000 ms |

The budgets are intentionally broad. They are designed to catch material regressions without making GitHub Actions depend on tiny runner-to-runner timing differences. They are not capacity targets or production SLOs.

CI writes `artifacts/phase2-performance.json`, uploads it as the `phase2-performance-<sha>` artifact, and includes the JSON baseline in the job summary. Later phases may add load, stress, soak, or capacity testing when real adapters and larger workloads justify it.

## Prometheus-compatible telemetry

The scrape boundary is:

```text
GET /metrics/
```

It is disabled by default. A controlled environment must both enable the boundary and configure a dedicated scrape credential:

```text
QBET_METRICS_ENABLED=true
QBET_METRICS_TOKEN=<dedicated-secret>
```

Scrapers authenticate with `Authorization: Bearer <dedicated-secret>`. Missing or incorrect credentials receive the same not-found response as a disabled endpoint so technical Monitoring is not exposed merely by enabling the feature flag. The token must be stored only in deployment/secrets configuration and must not be committed.

Hosted preview keeps the endpoint disabled explicitly.

The endpoint projects a rolling 15-minute window of persisted Monitoring records plus current durable queue state. Because these values are reconstructed from a moving window, event and duration counts are exposed as gauges rather than pretending to be monotonic process counters.

### Metric map

| Metric | Meaning | Future Grafana use |
| --- | --- | --- |
| `qbet_monitoring_events_window` | Monitoring event counts by engine, mode, stage, status, and level in the scrape window | throughput and stage/outcome panels |
| `qbet_stage_duration_observations_ms` | cumulative duration-bucket counts for the scrape window | latency/distribution panels |
| `qbet_queue_items` | current durable queue depth by mode and state | queue health/backlog panels |
| `qbet_operational_issues_window` | warning/error counts by level and stage | warning/error panels |
| `qbet_lifecycle_outcomes_window` | explicit Simulation/Execution lifecycle outcomes emitted by existing lifecycle records | mode outcome panels |

### Cardinality and privacy rules

Allowed labels are bounded operational dimensions such as engine, mode, stage, status/state, level, and duration bucket.

The metrics surface deliberately excludes:

- correlation IDs;
- user IDs;
- work IDs;
- opportunity IDs;
- account/provider identifiers;
- reason strings that may grow without a bounded category contract;
- credentials, tokens, cookies, sessions, raw payloads, or other secrets.

Detailed reasons and identifiers remain available only through Q-Bet's authorized PostgreSQL-backed Monitoring plane where they are needed for reconstruction.

## Grafana boundary

Grafana is a future visualization consumer, not a Q-Bet application dependency. Phase 3 may attach Prometheus/Grafana to this exposition boundary and evolve operational dashboards without moving authoritative workflow state out of PostgreSQL or coupling calculation/domain code to Grafana.

A production Grafana deployment, alert routing, long-term telemetry retention, and production capacity certification remain out of scope for Phase 2.

## Phase transition

Phase 2 should be marked complete in README/roadmap status only after issue #105 is reviewed and accepted with the connected E2E gate, performance job, metrics-readiness checks, and configured CI green. Until that acceptance, the Phase 3 integration register remains blocked as documented by the ticket.
