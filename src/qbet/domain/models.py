"""Core, provider-agnostic data structures for Q-Bet."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Currency = Literal["EUR", "GBP", "USD"]
PositiveDecimal = Annotated[Decimal, Field(gt=Decimal(0))]
NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal(0))]


class DomainModel(BaseModel):
    """Base model with immutable, normalized values at domain boundaries."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class OfferSide(StrEnum):
    BACK = "back"
    LAY = "lay"


class ExecutionStatus(StrEnum):
    PLANNED = "planned"
    REQUIRES_APPROVAL = "requires_approval"


class Event(DomainModel):
    id: Identifier
    sport: Identifier
    competition: Identifier
    participants: Annotated[tuple[Identifier, ...], Field(min_length=2)]
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def starts_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("starts_at must include timezone information")
        return value


class Market(DomainModel):
    id: Identifier
    event_id: Identifier
    name: Identifier
    outcomes: Annotated[tuple[Identifier, ...], Field(min_length=2)]


class Offer(DomainModel):
    id: Identifier
    market_id: Identifier
    selection: Identifier
    provider: Identifier
    side: OfferSide
    odds: Annotated[Decimal, Field(gt=Decimal(1))]
    available_stake: NonNegativeDecimal
    currency: Currency
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include timezone information")
        return value


class Opportunity(DomainModel):
    id: Identifier
    market_id: Identifier
    offer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2)]
    detected_at: datetime
    currency: Currency
    expected_profit: Decimal

    @field_validator("offer_ids")
    @classmethod
    def offer_ids_are_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("offer_ids must contain distinct identifiers")
        return value

    @field_validator("detected_at")
    @classmethod
    def detected_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("detected_at must include timezone information")
        return value


class StrategyResult(DomainModel):
    strategy: Identifier
    opportunity_id: Identifier
    stake: NonNegativeDecimal
    expected_profit: Decimal
    currency: Currency
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return value


class ExecutionStep(DomainModel):
    offer_id: Identifier
    stake: PositiveDecimal
    status: ExecutionStatus = ExecutionStatus.PLANNED


class ExecutionPlan(DomainModel):
    id: UUID
    strategy_result: StrategyResult
    steps: Annotated[tuple[ExecutionStep, ...], Field(min_length=1)]
    created_at: datetime
    requires_approval: bool = True
    notes: tuple[str, ...] = ()

    @field_validator("created_at")
    @classmethod
    def created_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return value
