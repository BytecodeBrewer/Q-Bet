from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from qbet.domain.ledger import PortfolioBalance
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle
from qbet.execution.service import ExecutionService
from qbet.execution.sandbox import valuation
from qbet.ledger import PortfolioLedger
from qbet.workflow.routing import RoutedWorkItem
from qbet.workflow.models import WorkflowMode


def proposal(*, expires_at: datetime | None = None) -> ExecutionProposal:
    request = BonusEngineRequest(
        opportunity_id="opportunity",
        inputs={
            "back_odds": Decimal("2"),
            "lay_odds": Decimal("2.1"),
            "back_stake": Decimal("10"),
            "exchange_commission": Decimal("0.02"),
            "stake_precision": Decimal("1"),
            "max_lay_liability": Decimal("100"),
        },
        currency="EUR",
        execution_offer_ids=("back", "lay"),
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    capital, payout = valuation(request)
    work = RoutedWorkItem(
        id=uuid4(),
        correlation_id=uuid4(),
        engine="bonus",
        mode=WorkflowMode.EXECUTION,
        opportunity_id=request.opportunity_id,
        capital_context="execution",
    )
    return ExecutionProposal(
        work=work,
        request=request,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=5),
        currency="EUR",
        capital_required=capital,
        payout=payout,
    )


def ledger() -> PortfolioLedger:
    return PortfolioLedger(
        balance=PortfolioBalance(mode="execution", currency="EUR", available=Decimal("1000"))
    )


def test_rejection_does_not_reserve_capital():
    record = ExecutionRecord(proposal=proposal())
    original = ledger()
    updated, state = ExecutionService().decide(
        record,
        original,
        actor="owner",
        owner="owner",
        approve=False,
        now=datetime.now(UTC),
    )
    assert updated.state is Lifecycle.REJECTED
    assert state == original


def test_approval_dispatch_and_settlement_are_explicit_and_deterministic():
    original = ledger()
    record, state = ExecutionService().decide(
        ExecutionRecord(proposal=proposal()),
        original,
        actor="owner",
        owner="owner",
        approve=True,
        now=datetime.now(UTC),
    )
    assert record.state is Lifecycle.SETTLED
    assert record.transitions == (
        Lifecycle.PROPOSED,
        Lifecycle.AWAITING_APPROVAL,
        Lifecycle.APPROVED,
        Lifecycle.DISPATCHED,
        Lifecycle.ACKNOWLEDGED,
        Lifecycle.SETTLED,
    )
    assert state.balance.available < original.balance.available
    assert state.balance.reserved == 0
    assert state.balance.locked == 0
    assert state.balance.pending == 0


def test_expired_proposal_is_rejected_before_reservation():
    original = ledger()
    record, state = ExecutionService().decide(
        ExecutionRecord(proposal=proposal(expires_at=datetime(2025, 1, 1, tzinfo=UTC))),
        original,
        actor="owner",
        owner="owner",
        approve=True,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert record.state is Lifecycle.REJECTED
    assert record.error == "proposal_expired"
    assert state == original


def test_approval_requires_authenticated_owner():
    record = ExecutionRecord(proposal=proposal())
    try:
        ExecutionService().decide(
            record,
            ledger(),
            actor="other",
            owner="owner",
            approve=True,
            now=datetime.now(UTC),
        )
    except PermissionError as error:
        assert str(error) == "proposal_owner_required"
    else:
        raise AssertionError("unauthorized approval was accepted")
