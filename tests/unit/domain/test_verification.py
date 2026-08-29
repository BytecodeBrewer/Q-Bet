from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from qbet.domain.verification import ProviderState, VerificationResult

def test_validates_provider_state_and_result_consistency() -> None:
    assert ProviderState(provider_id="book", active_bets_count=0, last_bet_timestamp=datetime.now(timezone.utc)).provider_id == "book"
    with pytest.raises(ValidationError): ProviderState(provider_id="book", active_bets_count=-1)
    with pytest.raises(ValidationError): ProviderState(provider_id="book", active_bets_count=0, last_bet_timestamp=datetime.now())
    with pytest.raises(ValidationError): VerificationResult(is_allowed=True, rejection_reason="nope")