from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from qbet.calculations import QualifyingBetInput
from qbet.domain.models import Currency, Identifier
from qbet.engines import BonusEngineRequest
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)


def bonus_request(
    opportunity_id: Identifier,
    *,
    generated_at: datetime,
    back_odds: Decimal = Decimal("2.5"),
    lay_odds: Decimal = Decimal("2.6"),
    back_stake: Decimal = Decimal("10"),
    exchange_commission: Decimal = Decimal("0.02"),
    stake_precision: Decimal = Decimal("0.01"),
    max_lay_liability: Decimal = Decimal("100"),
    execution_offer_ids: tuple[Identifier, Identifier] = ("book", "exchange"),
    currency: Currency = "EUR",
) -> BonusEngineRequest:
    """Build the common deterministic BonusEngine request used across workflow tests."""
    return BonusEngineRequest(
        opportunity_id=opportunity_id,
        inputs=QualifyingBetInput(
            back_odds=back_odds,
            lay_odds=lay_odds,
            back_stake=back_stake,
            exchange_commission=exchange_commission,
            stake_precision=stake_precision,
            max_lay_liability=max_lay_liability,
        ),
        currency=currency,
        execution_offer_ids=execution_offer_ids,
        generated_at=generated_at,
    )


def sandbox_revalidation_fixture(
    opportunity_id: Identifier,
    *,
    validated_at: datetime,
    outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    reason_code: Identifier = "fixture_revalidation",
) -> SandboxRevalidationFixture:
    return SandboxRevalidationFixture(
        opportunity_id=opportunity_id,
        outcome=outcome,
        validated_at=validated_at,
        reason_code=None if outcome is RevalidationOutcome.VALID else reason_code,
    )


def sandbox_result_fixture(
    opportunity_id: Identifier,
    *,
    observed_at: datetime,
    status: ResultStatus = ResultStatus.SUCCESS,
    reason_code: Identifier = "fixture_result_status",
    result_reference: Identifier = "sandbox-result",
) -> SandboxResultFixture:
    return SandboxResultFixture(
        opportunity_id=opportunity_id,
        status=status,
        observed_at=observed_at,
        reason_code=None if status is ResultStatus.SUCCESS else reason_code,
        result_reference=result_reference if status is ResultStatus.SUCCESS else None,
    )


def sandbox_mode_handlers(
    opportunity_id: Identifier,
    *,
    observed_at: datetime,
    simulation_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    execution_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    result_status: ResultStatus = ResultStatus.SUCCESS,
    simulation_reason_code: Identifier = "fixture_revalidation",
    execution_reason_code: Identifier = "fixture_revalidation",
    result_reason_code: Identifier = "fixture_result_status",
) -> ModeRequestHandlers:
    """Build isolated Simulation/Execution sandbox handlers with explicit overrides."""
    result = sandbox_result_fixture(
        opportunity_id,
        observed_at=observed_at,
        status=result_status,
        reason_code=result_reason_code,
    )
    return ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(
            revalidation_fixtures=(
                sandbox_revalidation_fixture(
                    opportunity_id,
                    validated_at=observed_at,
                    outcome=simulation_outcome,
                    reason_code=simulation_reason_code,
                ),
            ),
            result_fixtures=(result,),
        ),
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(
                sandbox_revalidation_fixture(
                    opportunity_id,
                    validated_at=observed_at,
                    outcome=execution_outcome,
                    reason_code=execution_reason_code,
                ),
            ),
            result_fixtures=(result,),
        ),
    )
