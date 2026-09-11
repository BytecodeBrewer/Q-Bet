from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.test import TransactionTestCase

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.monitoring import MonitoringQuery, MonitoringRecord
from qbet.storage.ledger import ModeWorkQueueRepository, RoutingConfigurationRepository
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration

pytestmark = pytest.mark.performance

_WORK_ITEMS = 20
_MONITORING_RECORDS = 100
_BUDGETS_MS = {
    "workflow_routing_schedule": 5_000.0,
    "postgres_queue_claim": 5_000.0,
    "monitoring_append_and_query": 5_000.0,
    "dashboard_projection": 5_000.0,
}


def _request(index: int, now: datetime) -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=f"performance-opportunity-{index}",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
        generated_at=now,
    )


def _elapsed_ms(callable_) -> tuple[float, object]:
    started = perf_counter()
    result = callable_()
    return (perf_counter() - started) * 1_000, result


def _write_artifact(payload: dict[str, object]) -> None:
    configured = os.environ.get("QBET_PERFORMANCE_ARTIFACT")
    if not configured:
        return
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Phase2PerformanceBaselineTests(TransactionTestCase):
    reset_sequences = True

    def test_representative_phase2_workloads_stay_inside_material_regression_budgets(self) -> None:
        now = datetime.now(UTC)
        routing = RoutingConfiguration(bonus=EngineModes(simulation=True, execution=True))
        RoutingConfigurationRepository().save(routing)
        queue = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(routing, queue_repository=queue)

        def schedule_work() -> None:
            for index in range(_WORK_ITEMS):
                scheduled = coordinator.schedule(
                    _request(index, now),
                    owner="performance-owner",
                    correlation_id=uuid4(),
                    scheduled_for=now,
                    expires_at=now + timedelta(minutes=5),
                )
                self.assertEqual(len(scheduled), 2)

        schedule_ms, _ = _elapsed_ms(schedule_work)
        queue_claim_ms, claimed = _elapsed_ms(lambda: queue.claim_due(now))
        self.assertEqual(len(claimed), _WORK_ITEMS * 2)

        monitoring = PostgresMonitoringRepository()

        def append_and_query() -> tuple[MonitoringRecord, ...]:
            for index in range(_MONITORING_RECORDS):
                monitoring.append(
                    MonitoringRecord(
                        correlation_id=uuid4(),
                        occurred_at=now + timedelta(milliseconds=index),
                        engine="bonus",
                        mode="simulation" if index % 2 == 0 else "execution",
                        stage="calculation",
                        event_type="stage",
                        status="completed",
                        duration_ms=25 + (index % 10),
                    )
                )
            return monitoring.list_records(
                MonitoringQuery(
                    start=now - timedelta(seconds=1),
                    end=now + timedelta(seconds=1),
                )
            )

        monitoring_ms, records = _elapsed_ms(append_and_query)
        self.assertEqual(len(records), _MONITORING_RECORDS)

        staff = User.objects.create_user(
            "performance-staff",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff)
        dashboard_ms, response = _elapsed_ms(lambda: self.client.get("/dashboard/"))
        self.assertEqual(response.status_code, 200)

        measurements = {
            "workflow_routing_schedule": round(schedule_ms, 3),
            "postgres_queue_claim": round(queue_claim_ms, 3),
            "monitoring_append_and_query": round(monitoring_ms, 3),
            "dashboard_projection": round(dashboard_ms, 3),
        }
        payload: dict[str, object] = {
            "schema_version": 1,
            "work_items": _WORK_ITEMS,
            "monitoring_records": _MONITORING_RECORDS,
            "measurements_ms": measurements,
            "regression_budgets_ms": _BUDGETS_MS,
        }
        _write_artifact(payload)
        print(
            "Phase 2 performance baseline (ms): "
            + ", ".join(f"{name}={value}" for name, value in measurements.items())
        )

        for name, measurement in measurements.items():
            self.assertLess(
                measurement,
                _BUDGETS_MS[name],
                msg=f"material performance regression in {name}: {measurement} ms",
            )
