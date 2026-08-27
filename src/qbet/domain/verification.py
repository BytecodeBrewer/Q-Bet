"""Typed verification contracts between strategy and allocation layers."""
from __future__ import annotations
from datetime import datetime
from typing import TypeAlias
from pydantic import field_validator, model_validator
from qbet.domain.models import DomainModel, Identifier
from qbet.engines.bonus import BonusEngineRequest
from qbet.engines.sports_capital import SportsCapitalEngineRequest

SportsOpportunityRequest: TypeAlias = BonusEngineRequest | SportsCapitalEngineRequest

class ProviderState(DomainModel):
    provider_id: Identifier
    active_bets_count: int
    last_bet_timestamp: datetime | None = None
    is_cooldown_active: bool = False

    @field_validator("active_bets_count")
    @classmethod
    def active_bets_are_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("active_bets_count must not be negative")
        return value

    @field_validator("last_bet_timestamp")
    @classmethod
    def last_bet_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("last_bet_timestamp must include timezone information")
        return value

class VerificationResult(DomainModel):
    is_allowed: bool
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def rejection_reason_matches_decision(self) -> "VerificationResult":
        if self.is_allowed and self.rejection_reason is not None:
            raise ValueError("allowed results must not include a rejection reason")
        if not self.is_allowed and self.rejection_reason is None:
            raise ValueError("blocked results must include a rejection reason")
        return self