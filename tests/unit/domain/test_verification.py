from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from qbet.domain.verification import (
    DomainRiskDecisionCode,
    DomainRiskPolicy,
    DomainRiskStatus,
    ProviderState,
    VerificationResult,
)


def test_validates_provider_state_and_result_consistency() -> None:
    assert (
        ProviderState(
            provider_id="book",
            active_bets_count=0,
            last_bet_timestamp=datetime.now(UTC),
        ).provider_id
        == "book"
    )
    with pytest.raises(ValidationError):
        ProviderState(provider_id="book", active_bets_count=-1)
    with pytest.raises(ValidationError):
        ProviderState(
            provider_id="book",
            active_bets_count=0,
            last_bet_timestamp=datetime.now(),  # noqa: DTZ005  # noqa: DTZ005
        )
    with pytest.raises(ValidationError):
        VerificationResult(is_allowed=True, rejection_reason="nope")
    with pytest.raises(ValidationError):
        DomainRiskPolicy(active_bet_warning_threshold=2, active_bet_rejection_threshold=2)


def test_structured_risk_result_requires_matching_status_and_code() -> None:
    warning = VerificationResult(
        is_allowed=True,
        status=DomainRiskStatus.WARN,
        decision_code=DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,
        warning_codes=(DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,),
    )
    assert warning.status is DomainRiskStatus.WARN
    with pytest.raises(ValidationError):
        VerificationResult(
            is_allowed=False,
            status=DomainRiskStatus.REJECT,
            decision_code=DomainRiskDecisionCode.ALLOWED,
            rejection_reason="not stable",
        )
