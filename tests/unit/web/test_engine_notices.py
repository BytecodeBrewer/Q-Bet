from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from qbet.layers.logging import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.engine_notices import BonusInputSnapshot, contextual_engine_statuses
from qbet.web.monitoring import MonitoringEngineStatus, MonitoringService
from qbet.web.provider_activity import ProviderActivitySnapshot
from qbet.web.simulation_control import SimulationRunSnapshot


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class _ReportStore:
    def __init__(
        self,
        reports: tuple[SimulationReport, ...],
        records: tuple[SimulationLogRecord, ...] = (),
    ) -> None:
        self._reports = reports
        self._records = records

    def list_recent_reports(self, limit: int = 50) -> tuple[SimulationReport, ...]:
        return self._reports[:limit]

    def load_records(self, run_id):
        return tuple(record for record in self._records if record.run_id == run_id)


def _engine(
    *,
    engine_id: str = "bonus",
    enabled: bool = True,
    live_state: str = "ready",
    detail: str = "Ready; no work running.",
    latest_no_opportunity: bool = False,
) -> MonitoringEngineStatus:
    return MonitoringEngineStatus(
        name="BonusEngine" if engine_id == "bonus" else "SportsCapitalEngine",
        status="gray",
        detail=detail,
        engine_id=engine_id,
        enabled=enabled,
        active=enabled,
        mode="simulation",
        live_state=live_state,
        latest_no_opportunity=latest_no_opportunity,
        latest_report_generated_at=NOW,
    )


def _run(reason_code: str) -> SimulationRunSnapshot:
    return SimulationRunSnapshot(
        run_id=uuid4(),
        engine="bonus",
        status="failed",
        portfolio_currency="EUR",
        progress=Decimal("0"),
        current_capital=Decimal("100"),
        report_id=None,
        error_message=reason_code,
    )


def _report(run_id) -> SimulationReport:
    return SimulationReport(
        run_id=run_id,
        config=SimulationRunConfig(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100"),
        ),
        engine="bonus",
        strategy_id=None,
        status=SimulationStatus.COMPLETED,
        starting_capital=Decimal("100"),
        current_capital=Decimal("100"),
        top_up_total=Decimal("0"),
        profit_loss=Decimal("0"),
        completed_steps=(),
        elapsed_duration=timedelta(seconds=1),
        progress=Decimal("1"),
        generated_at=NOW,
    )


def test_bonus_offer_count_guidance_is_not_projected_to_engine_notices() -> None:
    for snapshot in (
        BonusInputSnapshot(),
        BonusInputSnapshot(
            active_offers=5,
            ready_offers=3,
            provider_count=2,
            coverage_state="limited",
        ),
        BonusInputSnapshot(
            active_offers=16,
            ready_offers=15,
            provider_count=4,
            coverage_state="healthy",
        ),
    ):
        enriched = contextual_engine_statuses(
            (_engine(),),
            bonus_input=snapshot,
            bonus_offers_url="/bonus-offers/",
        )
        assert enriched[0].notices == ()


def test_bonus_input_read_failure_remains_a_real_engine_error() -> None:
    enriched = contextual_engine_statuses(
        (_engine(),),
        bonus_input=BonusInputSnapshot(available=False),
    )

    notice = enriched[0].notices[0]
    assert notice.severity == "error"
    assert notice.reason_code == "bonus_input_unavailable"
    assert notice.title == "Bonus input unavailable"



def test_provider_account_state_failure_is_distinct_from_missing_input() -> None:
    enriched = contextual_engine_statuses(
        (_engine(),),
        bonus_input=BonusInputSnapshot(
            active_offers=1,
            ready_offers=1,
            provider_count=1,
        ),
        runs=(_run("bonus_provider_state_unavailable"),),
    )

    notice = enriched[0].notices[0]

    assert notice.severity == "error"
    assert notice.reason_code == "bonus_provider_state_unavailable"
    assert notice.title == "Provider account state unavailable"
    assert "Bonus input" not in notice.title


def test_provider_runtime_failure_maps_to_contextual_error() -> None:
    enriched = contextual_engine_statuses(
        (_engine(engine_id="sports_capital"),),
        provider_activity=ProviderActivitySnapshot(
            state="unavailable",
            label="Market data source unavailable.",
            occurred_at=NOW,
            provider="the_odds_api",
            reason_code="simulation_odds_provider_unavailable",
        ),
    )

    notice = enriched[0].notices[0]

    assert notice.severity == "error"
    assert notice.title == "Market data source unavailable"
    assert "the_odds_api" not in notice.detail


def test_no_opportunity_is_information_not_error() -> None:
    enriched = contextual_engine_statuses(
        (_engine(engine_id="sports_capital", latest_no_opportunity=True),),
    )

    notice = enriched[0].notices[0]

    assert notice.severity == "info"
    assert notice.reason_code == "no_valid_opportunity"
    assert notice.title == "No valid opportunity found"


def test_completed_unprofitable_evaluation_drives_truthful_no_opportunity_flag() -> None:
    run_id = uuid4()
    evaluation = SimulationLogRecord(
        run_id=run_id,
        sequence=1,
        timestamp=NOW,
        record_type=SimulationLogRecordType.EVALUATION,
        source="simulation.workflow",
        payload={"is_profitable": False},
    )

    snapshot = MonitoringService(
        _ReportStore((_report(run_id),), (evaluation,))
    ).snapshot()

    bonus = next(engine for engine in snapshot.engines if engine.engine_id == "bonus")
    assert bonus.latest_no_opportunity is True
    assert bonus.latest_report_generated_at == NOW


def test_existing_technical_counters_remain_unchanged_by_notice_projection() -> None:
    original = MonitoringEngineStatus(
        name="BonusEngine",
        status="amber",
        detail="Current run warning.",
        engine_id="bonus",
        enabled=True,
        active=True,
        mode="simulation",
        live_state="warning",
        warning_count=7,
        error_count=2,
    )

    enriched = contextual_engine_statuses(
        (original,),
        bonus_input=BonusInputSnapshot(
            active_offers=1,
            ready_offers=1,
            provider_count=1,
        ),
    )[0]

    assert enriched.warning_count == 7
    assert enriched.error_count == 2
