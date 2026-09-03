from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from sqlite3 import Error as SQLiteError
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.simulation import (
    SimulationContext,
    SimulationEngine,
    SimulationRunConfig,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.storage import SQLiteSimulationReportStore
from qbet.web.models import SimulationAvailability, SimulationRunState

_ACTIVE_STATUSES = (
    SimulationRunState.Status.PENDING,
    SimulationRunState.Status.RUNNING,
)
_SUPPORTED_ENGINES = (
    SimulationEngine.BONUS,
    SimulationEngine.SPORTS_CAPITAL,
)


class SimulationControlError(RuntimeError):
    """Safe application-layer error that can be shown in the GUI."""


class SimulationDisabledError(SimulationControlError):
    pass


class SimulationAlreadyRunningError(SimulationControlError):
    pass


class SimulationDisableBlockedError(SimulationControlError):
    pass


class SimulationPersistenceUnavailableError(SimulationControlError):
    pass


@dataclass(frozen=True)
class SimulationAvailabilitySnapshot:
    enabled: bool
    persisted: bool


@dataclass(frozen=True)
class SimulationRunSnapshot:
    run_id: UUID
    engine: str
    status: str
    progress: Decimal
    current_capital: Decimal
    report_id: UUID | None
    error_message: str

    @property
    def is_active(self) -> bool:
        return self.status in {
            SimulationRunState.Status.PENDING,
            SimulationRunState.Status.RUNNING,
        }


@dataclass(frozen=True)
class SimulationControlSnapshot:
    availability: SimulationAvailabilitySnapshot
    runs: tuple[SimulationRunSnapshot, ...]

    @property
    def active_runs(self) -> tuple[SimulationRunSnapshot, ...]:
        return tuple(run for run in self.runs if run.is_active)


class SimulationControlService:
    """Typed application boundary between Django and simulation workflow execution."""

    def availability(self) -> SimulationAvailabilitySnapshot:
        try:
            state = SimulationAvailability.objects.filter(pk=1).first()
        except (OperationalError, ProgrammingError):
            return SimulationAvailabilitySnapshot(
                enabled=self._bootstrap_enabled(),
                persisted=False,
            )
        if state is None:
            return SimulationAvailabilitySnapshot(
                enabled=self._bootstrap_enabled(),
                persisted=False,
            )
        return SimulationAvailabilitySnapshot(enabled=state.enabled, persisted=True)

    def snapshot(self, *, limit: int = 10) -> SimulationControlSnapshot:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            rows = tuple(SimulationRunState.objects.all()[:limit])
        except (OperationalError, ProgrammingError):
            rows = ()
        return SimulationControlSnapshot(
            availability=self.availability(),
            runs=tuple(self._snapshot_from_row(row) for row in rows),
        )

    def set_enabled(self, enabled: bool) -> SimulationAvailabilitySnapshot:
        try:
            with transaction.atomic():
                state, _ = (
                    SimulationAvailability.objects.select_for_update().get_or_create(
                        pk=1,
                        defaults={"enabled": self._bootstrap_enabled()},
                    )
                )
                if not enabled and SimulationRunState.objects.filter(
                    status__in=_ACTIVE_STATUSES
                ).exists():
                    raise SimulationDisableBlockedError(
                        "Simulation cannot be disabled while a run is active."
                    )
                state.enabled = enabled
                state.save(update_fields=("enabled", "updated_at"))
        except SimulationDisableBlockedError:
            raise
        except (OperationalError, ProgrammingError) as error:
            raise SimulationControlError(
                "Simulation control state is unavailable. Run database migrations first."
            ) from error
        return SimulationAvailabilitySnapshot(enabled=enabled, persisted=True)

    def start(
        self,
        *,
        engine: SimulationEngine,
        starting_capital: Decimal,
        max_duration: timedelta,
    ) -> SimulationRunSnapshot:
        if engine not in _SUPPORTED_ENGINES:
            raise SimulationControlError("Only the two current sports engines are supported.")

        report_store = self._report_store()
        config = SimulationRunConfig(
            engine=engine,
            starting_capital=starting_capital,
            max_duration=max_duration,
        )
        run_id = uuid4()

        try:
            with transaction.atomic():
                availability, _ = (
                    SimulationAvailability.objects.select_for_update().get_or_create(
                        pk=1,
                        defaults={"enabled": self._bootstrap_enabled()},
                    )
                )
                if not availability.enabled:
                    raise SimulationDisabledError(
                        "Simulation is disabled by the administrator."
                    )
                if SimulationRunState.objects.filter(
                    engine=engine.value,
                    status__in=_ACTIVE_STATUSES,
                ).exists():
                    raise SimulationAlreadyRunningError(
                        f"A {engine.value} simulation is already active."
                    )
                SimulationRunState.objects.create(
                    run_id=run_id,
                    engine=engine.value,
                    status=SimulationRunState.Status.PENDING,
                    progress=Decimal("0"),
                    current_capital=starting_capital,
                )
        except (SimulationDisabledError, SimulationAlreadyRunningError):
            raise
        except (OperationalError, ProgrammingError) as error:
            raise SimulationControlError(
                "Simulation control state is unavailable. Run database migrations first."
            ) from error

        runner = WorkflowSimulationRunner(report_store=report_store)
        self._update_run(
            run_id,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0"),
            current_capital=starting_capital,
        )

        try:
            request = self._request_for(config=config, run_id=run_id)
            result = runner.run(
                request,
                on_step_completed=lambda context: self._record_progress(run_id, context),
            )
        except Exception as error:
            self._update_run(
                run_id,
                status=SimulationRunState.Status.FAILED,
                error_message="Simulation failed safely; inspect structured logs for details.",
            )
            raise SimulationControlError(
                "Simulation failed safely; no live execution was attempted."
            ) from error

        simulation_result = result.simulation_result
        report_id = runner.last_report.run_id if runner.last_report is not None else None
        self._update_run(
            run_id,
            status=simulation_result.status.value,
            progress=simulation_result.progress,
            current_capital=simulation_result.current_capital,
            report_id=report_id,
            error_message="",
        )
        row = SimulationRunState.objects.get(pk=run_id)
        return self._snapshot_from_row(row)

    def has_active_runs(self) -> bool:
        try:
            return SimulationRunState.objects.filter(
                status__in=_ACTIVE_STATUSES
            ).exists()
        except (OperationalError, ProgrammingError):
            return False

    @staticmethod
    def _snapshot_from_row(row: SimulationRunState) -> SimulationRunSnapshot:
        return SimulationRunSnapshot(
            run_id=row.run_id,
            engine=row.engine,
            status=row.status,
            progress=row.progress,
            current_capital=row.current_capital,
            report_id=row.report_id,
            error_message=row.error_message,
        )

    @staticmethod
    def _bootstrap_enabled() -> bool:
        return bool(getattr(settings, "QBET_SIMULATION_MODE_ENABLED", False))

    @staticmethod
    def _report_store() -> SQLiteSimulationReportStore:
        database_path = getattr(settings, "QBET_SIMULATION_REPORT_DB", None)
        if database_path is None:
            raise SimulationPersistenceUnavailableError(
                "Simulation report persistence is not configured."
            )
        try:
            return SQLiteSimulationReportStore(Path(database_path))
        except (OSError, SQLiteError) as error:
            raise SimulationPersistenceUnavailableError(
                "Simulation report persistence is unavailable."
            ) from error

    @staticmethod
    def _record_progress(run_id: UUID, context: SimulationContext) -> None:
        SimulationControlService._update_run(
            run_id,
            status=SimulationRunState.Status.RUNNING,
            progress=context.progress,
            current_capital=context.current_capital,
        )

    @staticmethod
    def _update_run(
        run_id: UUID,
        *,
        status: str | None = None,
        progress: Decimal | None = None,
        current_capital: Decimal | None = None,
        report_id: UUID | None = None,
        error_message: str | None = None,
    ) -> None:
        changes: dict[str, object] = {"updated_at": timezone.now()}
        if status is not None:
            changes["status"] = status
        if progress is not None:
            changes["progress"] = progress
        if current_capital is not None:
            changes["current_capital"] = current_capital
        if report_id is not None:
            changes["report_id"] = report_id
        if error_message is not None:
            changes["error_message"] = error_message
        SimulationRunState.objects.filter(pk=run_id).update(**changes)

    @staticmethod
    def _request_for(
        *,
        config: SimulationRunConfig,
        run_id: UUID,
    ) -> WorkflowSimulationRequest:
        generated_at = datetime.now(UTC)
        opportunities: tuple[BonusEngineRequest | SportsCapitalEngineRequest, ...]
        if config.engine is SimulationEngine.BONUS:
            opportunities = tuple(
                BonusEngineRequest(
                    opportunity_id=f"gui-{run_id}-bonus-{index}",
                    inputs=QualifyingBetInput(
                        back_odds=Decimal("2.50"),
                        lay_odds=Decimal("2.60"),
                        back_stake=Decimal("10"),
                        exchange_commission=Decimal("0.02"),
                        stake_precision=Decimal("0.01"),
                        max_lay_liability=Decimal("1000"),
                    ),
                    currency="EUR",
                    execution_offer_ids=(
                        f"gui-book-{index}",
                        f"gui-exchange-{index}",
                    ),
                    generated_at=generated_at,
                )
                for index in range(1, 3)
            )
        elif config.engine is SimulationEngine.SPORTS_CAPITAL:
            def offer(outcome: str) -> ArbitrageOffer:
                return ArbitrageOffer(
                    outcome=outcome,
                    odds=Decimal("2.20"),
                    available_liquidity=Decimal("1000"),
                    stake_precision=Decimal("0.01"),
                    currency="EUR",
                )

            opportunities = tuple(
                SportsCapitalEngineRequest(
                    opportunity_id=f"gui-{run_id}-sports-{index}",
                    inputs=TwoWayArbitrageInput(
                        first_offer=offer(f"home-{index}"),
                        second_offer=offer(f"away-{index}"),
                        requested_total_stake=Decimal("20"),
                    ),
                    currency="EUR",
                    execution_offer_ids=(
                        f"gui-home-{index}",
                        f"gui-away-{index}",
                    ),
                    generated_at=generated_at,
                )
                for index in range(1, 3)
            )
        else:
            raise SimulationControlError("Unsupported simulation engine.")

        return WorkflowSimulationRequest(
            config=config,
            opportunities=opportunities,
            provider_state=ProviderState(
                provider_id="gui-sandbox-provider",
                active_bets_count=0,
            ),
            correlation_id=run_id,
        )
