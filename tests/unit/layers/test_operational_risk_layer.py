from datetime import UTC
from decimal import Decimal
from uuid import UUID

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.verification import (
    DomainRiskDecisionCode,
    DomainRiskPolicy,
    DomainRiskStatus,
    ProviderState,
)
from qbet.engines.bonus import BonusEngineRequest
from qbet.engines.sports_capital import SportsCapitalEngineRequest
from qbet.layers import SimulationLogContext, SimulationLogRecordType
from qbet.layers.verification import (
    COOLDOWN_ACTIVE_REASON,
    FREQUENCY_LIMIT_REASON,
    OperationalRiskLayer,
)
from qbet.storage.postgres import PostgresProviderStateRepository


def bonus_request() -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id="bonus-opportunity",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal(10),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal(100),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
    )


def sports_request() -> SportsCapitalEngineRequest:
    def offer(outcome):
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal(100),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    return SportsCapitalEngineRequest(
        opportunity_id="sports-opportunity",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal(100),
        ),
        currency="EUR",
        execution_offer_ids=("home", "away"),
    )


def state(**changes: object) -> ProviderState:
    values = {
        "provider_id": "book",
        "active_bets_count": 0,
        "is_cooldown_active": False,
    }
    values.update(changes)
    return ProviderState(**values)


def test_default_policy_allows_warns_and_rejects_both_engine_request_types() -> None:
    layer = OperationalRiskLayer()
    assert layer.verify_opportunity(bonus_request(), state()).status is DomainRiskStatus.ALLOW
    warning = layer.verify_opportunity(sports_request(), state(active_bets_count=1))
    assert warning.status is DomainRiskStatus.WARN
    assert warning.warning_codes == (DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,)
    rejected = layer.verify_opportunity(bonus_request(), state(active_bets_count=2))
    assert rejected.status is DomainRiskStatus.REJECT
    assert rejected.decision_code is DomainRiskDecisionCode.FREQUENCY_LIMIT
    assert rejected.rejection_reason == FREQUENCY_LIMIT_REASON
    cooldown = layer.verify_opportunity(sports_request(), state(is_cooldown_active=True))
    assert cooldown.decision_code is DomainRiskDecisionCode.COOLDOWN_ACTIVE
    assert cooldown.rejection_reason == COOLDOWN_ACTIVE_REASON


def test_custom_thresholds_are_deterministic() -> None:
    layer = OperationalRiskLayer(
        DomainRiskPolicy(active_bet_warning_threshold=2, active_bet_rejection_threshold=3)
    )
    assert (
        layer.verify_opportunity(bonus_request(), state(active_bets_count=1)).status
        is DomainRiskStatus.ALLOW
    )
    assert (
        layer.verify_opportunity(bonus_request(), state(active_bets_count=2)).status
        is DomainRiskStatus.WARN
    )
    assert (
        layer.verify_opportunity(bonus_request(), state(active_bets_count=3)).status
        is DomainRiskStatus.REJECT
    )


def test_structured_decision_logs_with_the_workflow_correlation_id() -> None:
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    log_context = SimulationLogContext(run_id=correlation_id)
    result = OperationalRiskLayer().verify_opportunity(
        bonus_request(),
        state(active_bets_count=1),
        log_context=log_context,
        correlation_id=correlation_id,
    )
    assert result.status is DomainRiskStatus.WARN
    record = log_context.records[0]
    assert record.run_id == correlation_id
    assert record.record_type is SimulationLogRecordType.RISK_DECISION
    assert record.payload["decision_code"] == DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING


def test_repository_injection_reloads_provider_state_and_rechecks_unknown_provider() -> None:
    from datetime import datetime

    provider_id = "risk-layer-repository-test-book"
    repository = PostgresProviderStateRepository()
    layer = OperationalRiskLayer(provider_state_repository=repository)

    unknown = layer.verify_opportunity(bonus_request(), provider_id=provider_id)
    assert unknown.status is DomainRiskStatus.RECHECK
    assert unknown.decision_code is DomainRiskDecisionCode.RECHECK_REQUIRED

    repository.upsert(
        ProviderState(
            provider_id=provider_id,
            active_bets_count=1,
            last_bet_timestamp=datetime(2026, 8, 30, tzinfo=UTC),
            is_cooldown_active=False,
        )
    )
    reloaded_layer = OperationalRiskLayer(
        provider_state_repository=PostgresProviderStateRepository()
    )

    assert (
        reloaded_layer.verify_opportunity(bonus_request(), provider_id=provider_id).status
        is DomainRiskStatus.WARN
    )

    repository.upsert(
        ProviderState(
            provider_id=provider_id,
            active_bets_count=2,
            is_cooldown_active=True,
        )
    )

    assert (
        reloaded_layer.verify_opportunity(bonus_request(), provider_id=provider_id).decision_code
        is DomainRiskDecisionCode.FREQUENCY_LIMIT
    )
