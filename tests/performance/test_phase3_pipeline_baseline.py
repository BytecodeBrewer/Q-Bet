from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngine, BonusEngineRequest
from qbet.monitoring import MonitoringLevel, MonitoringQuery, MonitoringRecord
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)
from qbet.storage.ledger import ModeWorkQueueRepository
from qbet.storage.models import ModeWorkQueueRow
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(
        os.getenv("QBET_RUN_PERFORMANCE") != "1",
        reason="Phase 3 performance baselines are opt-in",
    ),
]

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
ROUTING_ITEMS = 20
QUEUE_ITEMS = 40
MONITORING_RECORDS = 100
ENGINE_EVALUATIONS = 500


def _request(index: int) -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=f"perf-opportunity-{index}",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.50"),
            lay_odds=Decimal("2.60"),
            back_stake=Decimal("10.00"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100.00"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
        generated_at=NOW,
    )


def _handlers(count: int) -> ModeRequestHandlers:
    opportunity_ids = tuple(f"perf-opportunity-{index}" for index in range(count))
    revalidation = tuple(
        SandboxRevalidationFixture(
            opportunity_id=opportunity_id,
            outcome=RevalidationOutcome.VALID,
            validated_at=NOW,
        )
        for opportunity_id in opportunity_ids
    )
    results = tuple(
        SandboxResultFixture(
            opportunity_id=opportunity_id,
            status=ResultStatus.SUCCESS,
            observed_at=NOW,
            result_reference=f"result-{opportunity_id}",
        )
        for opportunity_id in opportunity_ids
    )
    return ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(
            revalidation_fixtures=revalidation,
            result_fixtures=results,
        ),
        execution=ExecutionSandboxRequestHandler(),
    )


def _measurement(name: str, work_items: int, duration_seconds: float) -> dict[str, object]:
    duration_ms = duration_seconds * 1000
    throughput = work_items / duration_seconds if duration_seconds > 0 else None
    return {
        "name": name,
        "work_items": work_items,
        "duration_ms": round(duration_ms, 3),
        "throughput_per_second": None if throughput is None else round(throughput, 3),
    }


class Phase3PipelinePerformanceBaselineTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.staff = get_user_model().objects.create_user(
            "phase3-perf-admin",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.routing = RoutingConfiguration(bonus=EngineModes(simulation=True))

    def test_connected_pipeline_baseline(self) -> None:
        measurements: list[dict[str, object]] = []

        coordinator = ModeDispatchCoordinator(
            self.routing,
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=_handlers(ROUTING_ITEMS),
        )
        started = perf_counter()
        for index in range(ROUTING_ITEMS):
            coordinator.schedule(
                _request(index),
                owner="phase3-performance",
                correlation_id=UUID(int=index + 1),
                scheduled_for=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
        dispatched = coordinator.dispatch_due(now=NOW, owner="phase3-performance")
        duration = perf_counter() - started
        self.assertEqual(len(dispatched), ROUTING_ITEMS)
        measurements.append(_measurement("workflow_schedule_and_dispatch", ROUTING_ITEMS, duration))

        ModeWorkQueueRow.objects.all().delete()
        queue_coordinator = ModeDispatchCoordinator(
            self.routing,
            queue_repository=ModeWorkQueueRepository(),
        )
        started = perf_counter()
        for index in range(QUEUE_ITEMS):
            queue_coordinator.schedule(
                _request(index),
                owner="phase3-queue-performance",
                correlation_id=UUID(int=1000 + index),
                scheduled_for=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
        claimed = ModeWorkQueueRepository().claim_due(NOW)
        duration = perf_counter() - started
        self.assertEqual(len(claimed), QUEUE_ITEMS)
        measurements.append(_measurement("postgres_queue_persist_and_claim", QUEUE_ITEMS, duration))

        monitoring = PostgresMonitoringRepository()
        monitoring_start = NOW - timedelta(minutes=1)
        monitoring_end = NOW + timedelta(minutes=1)
        started = perf_counter()
        for index in range(MONITORING_RECORDS):
            monitoring.append(
                MonitoringRecord(
                    correlation_id=UUID(int=10_000 + index),
                    occurred_at=NOW,
                    engine="bonus",
                    mode="simulation",
                    stage="calculation",
                    event_type="performance_fixture",
                    status="allow",
                    level=MonitoringLevel.INFO,
                    duration_ms=index % 10,
                    references={"opportunity_id": f"monitoring-{index}"},
                )
            )
        records = monitoring.list_records(
            MonitoringQuery(start=monitoring_start, end=monitoring_end)
        )
        duration = perf_counter() - started
        self.assertGreaterEqual(len(records), MONITORING_RECORDS)
        measurements.append(
            _measurement("monitoring_append_and_bounded_query", MONITORING_RECORDS, duration)
        )

        self.client.force_login(self.staff)
        started = perf_counter()
        export = self.client.get(
            "/monitoring/export/json/",
            {
                "start": monitoring_start.isoformat(),
                "end": monitoring_end.isoformat(),
                "view": "extended",
            },
        )
        duration = perf_counter() - started
        self.assertEqual(export.status_code, 200)
        self.assertGreaterEqual(len(export.json()), MONITORING_RECORDS)
        measurements.append(_measurement("monitoring_json_projection", MONITORING_RECORDS, duration))

        engine = BonusEngine()
        engine_requests = tuple(_request(index) for index in range(ENGINE_EVALUATIONS))
        started = perf_counter()
        evaluations = tuple(engine.evaluate(request) for request in engine_requests)
        duration = perf_counter() - started
        self.assertEqual(len(evaluations), ENGINE_EVALUATIONS)
        measurements.append(_measurement("bonus_engine_evaluation_batch", ENGINE_EVALUATIONS, duration))

        result = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "internal_phase3_baseline",
            "database": "isolated_postgresql",
            "external_provider_latency_included": False,
            "notes": [
                "Wall-clock measurements are comparative baselines, not production SLAs.",
                "Provider, network, browser, exchange, and crypto venue latency is excluded.",
                "No microsecond-level regression threshold is enforced by this suite.",
            ],
            "measurements": measurements,
        }
        output_path = Path(
            os.getenv(
                "QBET_PERFORMANCE_OUTPUT",
                "artifacts/phase3-performance-baseline.json",
            )
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        print("\nPhase 3 performance baseline")
        for measurement in measurements:
            print(
                f"- {measurement['name']}: {measurement['duration_ms']} ms "
                f"for {measurement['work_items']} items "
                f"({measurement['throughput_per_second']} items/s)"
            )
        print(f"Artifact: {output_path}")
