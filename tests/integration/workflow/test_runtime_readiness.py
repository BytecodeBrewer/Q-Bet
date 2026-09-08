from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TransactionTestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.storage.models import ModeWorkQueueRow, MonitoringRecordRow, PortfolioLedgerRow
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowMode, WorkflowStage
from qbet.workflow.readiness import PIPELINE_STAGES, StageReadiness
from qbet.workflow.routing import EngineModes, RoutingConfiguration, V1Engine

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


class _BlockedReadiness:
    def snapshot(
        self,
        engine: V1Engine,
        mode: WorkflowMode,
    ) -> tuple[StageReadiness, ...]:
        del engine, mode
        return tuple(
            StageReadiness(
                stage=stage,
                ready=stage is not WorkflowStage.ENGINE_PREPARATION,
                reason=(
                    "builder_unavailable"
                    if stage is WorkflowStage.ENGINE_PREPARATION
                    else None
                ),
            )
            for stage in PIPELINE_STAGES
        )


def _request() -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id="readiness-bonus",
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
        generated_at=NOW,
    )


class RuntimeReadinessTests(TransactionTestCase):
    def test_not_ready_stage_blocks_pipeline_before_mode_side_effects(self) -> None:
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True)),
            readiness_provider=_BlockedReadiness(),
        )
        scheduled = coordinator.schedule(
            _request(),
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        dispatched = coordinator.dispatch_due(now=NOW, owner="owner")

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].state.value, "recheck")
        self.assertEqual(
            dispatched[0].history[-1].reason,
            "pipeline_not_ready:engine_preparation",
        )
        self.assertEqual(ModeWorkQueueRow.objects.get().state, "recheck")
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)
        payloads = tuple(MonitoringRecordRow.objects.values_list("payload", flat=True))
        self.assertTrue(
            any(
                payload.get("event_type") == "readiness"
                and payload.get("stage") == "engine_preparation"
                and payload.get("status") == "not_ready"
                for payload in payloads
            )
        )
