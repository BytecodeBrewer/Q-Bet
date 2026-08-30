"""Typed verification contracts between strategy and allocation layers."""
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias
from pydantic import Field, field_validator, model_validator
from qbet.domain.models import DomainModel, Identifier
from qbet.engines.bonus import BonusEngineRequest
from qbet.engines.sports_capital import SportsCapitalEngineRequest

SportsOpportunityRequest: TypeAlias = BonusEngineRequest | SportsCapitalEngineRequest

class DomainRiskStatus(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    RECHECK = "recheck"
    REJECT = "reject"

class DomainRiskDecisionCode(StrEnum):
    ALLOWED = "allowed"
    ACTIVE_BET_LIMIT_APPROACHING = "provider_active_bet_limit_approaching"
    FREQUENCY_LIMIT = "provider_frequency_limit"
    COOLDOWN_ACTIVE = "provider_cooldown_active"

class DomainRiskPolicy(DomainModel):
    active_bet_warning_threshold: int = Field(default=1, ge=0)
    active_bet_rejection_threshold: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def warning_threshold_precedes_rejection(self) -> "DomainRiskPolicy":
        if self.active_bet_warning_threshold >= self.active_bet_rejection_threshold:
            raise ValueError("active_bet_warning_threshold must be below active_bet_rejection_threshold")
        return self

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
    status: DomainRiskStatus = DomainRiskStatus.ALLOW
    decision_code: DomainRiskDecisionCode = DomainRiskDecisionCode.ALLOWED
    warning_codes: tuple[DomainRiskDecisionCode, ...] = ()
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def decision_matches_status(self) -> "VerificationResult":
        if self.status is DomainRiskStatus.REJECT:
            if self.is_allowed or self.rejection_reason is None or self.decision_code not in {DomainRiskDecisionCode.FREQUENCY_LIMIT, DomainRiskDecisionCode.COOLDOWN_ACTIVE}:
                raise ValueError("rejected results require a stable rejection decision")
        elif not self.is_allowed or self.rejection_reason is not None:
            raise ValueError("non-rejected results must be allowed without a rejection reason")
        if self.status is DomainRiskStatus.WARN and not self.warning_codes:
            raise ValueError("warning results require warning codes")
        if self.status is not DomainRiskStatus.WARN and self.warning_codes:
            raise ValueError("only warning results may include warning codes")
        return self
