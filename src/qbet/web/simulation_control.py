from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from pydantic import ValidationError

from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.simulation import (
    SimulationContext,
    SimulationEngine,
    SimulationRunConfig,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.simulation.opportunity_source import (
    DeterministicSimulationOpportunitySource,
    SimulationOpportunitySource,
    SimulationOpportunitySourceError,
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository
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

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SimulationDisabledError(SimulationControlError):
    pass


class SimulationAlreadyRunningError(SimulationControlError):
    pass


class SimulationDisableBlockedError(SimulationControlError):
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

    def __init__(self, *, opportunity_source: SimulationOpportunitySource | None = None) -> None:
        self._opportunity_source = opportunity_source

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
                state, _ = SimulationAvailability.objects.select_for_update().get_or_create(
                    pk=1,
                    defaults={"enabled": self._bootstrap_enabled()},
                )
                if (
                    not enabled
                    and SimulationRunState.objects.filter(status__in=_ACTIVE_STATUSES).exists()
                ):
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
                availability, _ = SimulationAvailability.objects.select_for_update().get_or_create(
                    pk=1,
                    defaults={"enabled": self._bootstrap_enabled()},
                )
                if not availability.enabled:
                    raise SimulationDisabledError("Simulation is disabled by the administrator.")
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

        ledger_repository = SimulationPortfolioLedgerRepository()
        initial_ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="simulation",
                currency="EUR",
                available=starting_capital,
            )
        )
        simulation_ledger = ledger_repository.load_or_create(initial_ledger)
        effective_config = config.model_copy(
            update={"starting_capital": simulation_ledger.balance.available}
        )
        runner = WorkflowSimulationRunner(
            report_store=report_store,
            simulation_ledger=simulation_ledger,
            ledger_writer=ledger_repository.merge,
        )
        self._update_run(
            run_id,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0"),
            current_capital=simulation_ledger.balance.available,
        )

        try:
            source = self._opportunity_source or self._configured_opportunity_source(engine)
            bundle = source.build(effective_config, run_id)
            request = WorkflowSimulationRequest(
                config=effective_config,
                opportunities=bundle.opportunities,
                provider_state=bundle.provider_state,
                correlation_id=run_id,
                customer_report_input=bundle.customer_report_input,
            )
            result = runner.run(
                request,
                on_step_completed=lambda context: self._record_progress(run_id, context),
            )
        except SimulationOpportunitySourceError as error:
            self._update_run(
                run_id,
                status=SimulationRunState.Status.FAILED,
                error_message=error.reason_code,
            )
            raise SimulationControlError(
                error.user_message,
                reason_code=error.reason_code,
            ) from None
        except SimulationControlError as error:
            self._update_run(
                run_id,
                status=SimulationRunState.Status.FAILED,
                error_message=error.reason_code or "simulation_configuration_invalid",
            )
            raise
        except Exception as error:
            self._update_run(
                run_id,
                status=SimulationRunState.Status.FAILED,
                error_message="simulation_failed",
            )
            raise SimulationControlError(
                "Simulation failed safely; no live execution was attempted.",
                reason_code="simulation_failed",
            ) from error

        simulation_result = result.simulation_result
        report_id = runner.last_report.run_id if runner.last_report is not None else None
        persisted_ledger = runner.last_ledger
        if persisted_ledger is None:
            self._update_run(
                run_id,
                status=SimulationRunState.Status.FAILED,
                error_message="Simulation ledger state is unavailable.",
            )
            raise SimulationControlError("Simulation ledger state is unavailable.")
        self._update_run(
            run_id,
            status=simulation_result.status.value,
            progress=simulation_result.progress,
            current_capital=persisted_ledger.balance.available,
            report_id=report_id,
            error_message="",
        )
        row = SimulationRunState.objects.get(pk=run_id)
        return self._snapshot_from_row(row)

    def has_active_runs(self) -> bool:
        try:
            return SimulationRunState.objects.filter(status__in=_ACTIVE_STATUSES).exists()
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
    def _report_store() -> PostgresSimulationReportStore:
        return PostgresSimulationReportStore()

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
    def _configured_opportunity_source(
        engine: SimulationEngine,
    ) -> SimulationOpportunitySource:
        if engine is SimulationEngine.BONUS:
            return DeterministicSimulationOpportunitySource()

        source_mode = str(
            getattr(settings, "QBET_SIMULATION_SPORTS_SOURCE", "fixture")
        ).strip().lower()
        if source_mode in {"", "fixture"}:
            return DeterministicSimulationOpportunitySource()
        if source_mode != "the_odds_api":
            raise SimulationControlError(
                "Configured SportsCapital Simulation source is invalid.",
                reason_code="simulation_source_invalid",
            )

        values = {
            "sport": getattr(settings, "QBET_SIMULATION_ODDS_SPORT", ""),
            "event_id": getattr(settings, "QBET_SIMULATION_ODDS_EVENT_ID", ""),
            "market": getattr(settings, "QBET_SIMULATION_ODDS_MARKET", ""),
            "assumed_liquidity": getattr(
                settings, "QBET_SIMULATION_ASSUMED_LIQUIDITY", ""
            ),
            "requested_total_stake": getattr(
                settings, "QBET_SIMULATION_REQUESTED_TOTAL_STAKE", ""
            ),
            "stake_precision": getattr(
                settings, "QBET_SIMULATION_STAKE_PRECISION", ""
            ),
        }
        if any(not str(value).strip() for value in values.values()):
            raise SimulationControlError(
                "Connected SportsCapital Simulation is not fully configured.",
                reason_code="simulation_market_configuration_missing",
            )
        try:
            connected = TheOddsApiSportsSimulationConfig.model_validate(values)
        except ValidationError:
            raise SimulationControlError(
                "Connected SportsCapital Simulation configuration is invalid.",
                reason_code="simulation_market_configuration_invalid",
            ) from None
        return TheOddsApiSportsSimulationOpportunitySource(connected)
