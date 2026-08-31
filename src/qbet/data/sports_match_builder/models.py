"""Typed metadata and context for sports match building."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import Field

from qbet.calculations import DutchingTargetMode, FreeBetStakeReturn
from qbet.domain.models import (
    DomainModel,
    Identifier,
    NonNegativeDecimal,
    PositiveDecimal,
)
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest


class SportsMatchContext(DomainModel):
    """Source identity retained alongside an engine-owned request."""

    correlation_id: UUID
    snapshot_id: Identifier
    provider_id: Identifier
    source_id: Identifier
    event_id: Identifier
    market_id: Identifier


class BuiltBonusMatch(DomainModel):
    request: BonusEngineRequest
    context: SportsMatchContext


class BuiltSportsCapitalMatch(DomainModel):
    request: SportsCapitalEngineRequest
    context: SportsMatchContext


class QualifyingBetMatchMetadata(DomainModel):
    back_offer_id: Identifier
    lay_offer_id: Identifier
    back_stake: PositiveDecimal
    exchange_commission: Decimal = Field(
        ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )
    stake_precision: PositiveDecimal
    max_lay_liability: NonNegativeDecimal
    minimum_lay_available_stake: PositiveDecimal


class FreeBetMatchMetadata(DomainModel):
    back_offer_id: Identifier
    lay_offer_id: Identifier
    free_bet_amount: PositiveDecimal
    exchange_commission: Decimal = Field(
        ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )
    stake_precision: PositiveDecimal
    minimum_lay_available_stake: PositiveDecimal
    stake_return_rule: FreeBetStakeReturn


class TwoWayArbitrageMatchMetadata(DomainModel):
    first_offer_id: Identifier
    second_offer_id: Identifier
    requested_total_stake: PositiveDecimal
    first_stake_precision: PositiveDecimal
    second_stake_precision: PositiveDecimal
    first_fee_rate: Decimal = Field(
        default=Decimal(0), ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )
    second_fee_rate: Decimal = Field(
        default=Decimal(0), ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )


class DutchingMatchMetadata(DomainModel):
    offer_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=4)
    target_mode: DutchingTargetMode
    total_stake: PositiveDecimal | None = None
    target_return: PositiveDecimal | None = None
    minimum_available_stake: PositiveDecimal
    stake_precisions: tuple[PositiveDecimal, ...] = Field(min_length=2, max_length=4)
    fee_rates: tuple[Decimal, ...] = Field(min_length=2, max_length=4)

    def model_post_init(self, __context: object) -> None:
        if len(set(self.offer_ids)) != len(self.offer_ids):
            raise ValueError("offer_ids must be distinct")
        if len(self.stake_precisions) != len(self.offer_ids):
            raise ValueError("stake_precisions must match offer_ids")
        if len(self.fee_rates) != len(self.offer_ids):
            raise ValueError("fee_rates must match offer_ids")
        if any(
            rate < Decimal(0) or rate >= Decimal(1) or not rate.is_finite()
            for rate in self.fee_rates
        ):
            raise ValueError(
                "fee_rates must be finite values from 0 inclusive to 1 exclusive"
            )
        if self.target_mode is DutchingTargetMode.TOTAL_STAKE:
            if self.total_stake is None or self.target_return is not None:
                raise ValueError("total_stake mode requires only total_stake")
        elif self.target_return is None or self.total_stake is not None:
            raise ValueError("target_return mode requires only target_return")
