from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier, Event, Thread
from uuid import uuid4
from unittest.mock import Mock

from django.db import close_old_connections
from django.test import TransactionTestCase

from qbet.calculations import ArbitrageOffer, TwoWayArbitrageInput
from qbet.domain.ledger import LedgerCommand, PortfolioBalance
from qbet.domain.verification import ProviderState
from qbet.engines import SportsCapitalEngineRequest
from qbet.ledger import PortfolioLedger
from qbet.orchestrator import RejectionReason
from qbet.simulation import (
    SimulationEngine,
    SimulationRunConfig,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository
from qbet.workflow import WorkflowDecision, WorkflowStage
from tests.support.workflow import bonus_request


class _ContendingReserver:
    """Hold the winning run after reserve until the losing liquidity decision exists."""

    def __init__(self, *, start: Barrier, loser_decided: Event) -> None:
        self._start = start
        self._loser_decided = loser_decided

    def __call__(self, command: LedgerCommand):
        self._start.wait(timeout=10)
        ledger, decision = SimulationPortfolioLedgerRepository().reserve_with_liquidity(
            command
        )
        if decision.decision is WorkflowDecision.ALLOW:
            if not self._loser_decided.wait(timeout=10):
                raise TimeoutError("competing liquidity decision did not complete")
        else:
            self._loser_decided.set()
        return ledger, decision


def _sports_request(now: datetime) -> SportsCapitalEngineRequest:
    def offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.20"),
            available_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    return SportsCapitalEngineRequest(
        opportunity_id="concurrent-sports",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal("20"),
        ),
        currency="EUR",
        execution_offer_ids=("sports-home", "sports-away"),
        generated_at=now,
    )


def _request(engine, opportunity, *, correlation_id):
    return WorkflowSimulationRequest(
        config=SimulationRunConfig(
            engine=engine,
            starting_capital=Decimal("20"),
        ),
        opportunities=(opportunity,),
        provider_state=ProviderState(
            provider_id="book",
            active_bets_count=0,
        ),
        correlation_id=correlation_id,
    )


class SimulationSharedLiquidityConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_atomic_product_path_rejects_foreign_currency_without_reserving(self) -> None:
        now = datetime.now(UTC)
        for currency in ("EUR", "GBP", "USD"):
            for engine, opportunity in (
                (SimulationEngine.BONUS, bonus_request("currency-bonus", generated_at=now)),
                (SimulationEngine.SPORTS_CAPITAL, _sports_request(now)),
            ):
                with self.subTest(currency=currency, engine=engine):
                    repository = SimulationPortfolioLedgerRepository()
                    initial = repository.load_or_create(
                        PortfolioLedger(
                            balance=PortfolioBalance(
                                mode="simulation", currency=currency, available=Decimal("100"),
                            )
                        )
                    )
                    reserver = Mock(wraps=repository.reserve_with_liquidity)
                    writer = Mock(wraps=repository.merge)
                    runner = WorkflowSimulationRunner(
                        simulation_ledger=initial,
                        ledger_writer=writer,
                        liquidity_reserver=reserver,
                    )
                    result = runner.run(_request(engine, opportunity, correlation_id=uuid4()))
                    workflow = result.workflow_results[0]
                    transition = next(
                        item for item in workflow.transitions
                        if item.stage is WorkflowStage.LIQUIDITY_CHECK
                    )
                    persisted = repository.load(currency=currency)
                    if currency == "EUR":
                        self.assertEqual(workflow.final_decision, WorkflowDecision.ALLOW)
                        reserver.assert_called_once()
                        self.assertNotEqual(persisted, initial)
                    else:
                        self.assertEqual(workflow.final_decision, WorkflowDecision.REJECT)
                        self.assertEqual(transition.reason, "currency_mismatch")
                        reserver.assert_not_called()
                        writer.assert_not_called()
                        self.assertEqual(persisted, initial)

    def test_bonus_and_sports_contend_at_authoritative_liquidity_boundary(self) -> None:
        now = datetime.now(UTC)
        repository = SimulationPortfolioLedgerRepository()
        repository.load_or_create(
            PortfolioLedger(
                balance=PortfolioBalance(
                    mode="simulation",
                    currency="EUR",
                    available=Decimal("20"),
                )
            )
        )
        stale_bonus = repository.load(currency="EUR")
        stale_sports = repository.load(currency="EUR")
        assert stale_bonus is not None and stale_sports is not None
        self.assertEqual(stale_bonus.balance.available, Decimal("20"))
        self.assertEqual(stale_sports.balance.available, Decimal("20"))

        start = Barrier(2)
        loser_decided = Event()
        bonus_runner = WorkflowSimulationRunner(
            simulation_ledger=stale_bonus,
            ledger_writer=SimulationPortfolioLedgerRepository().merge,
            liquidity_reserver=_ContendingReserver(
                start=start,
                loser_decided=loser_decided,
            ),
        )
        sports_runner = WorkflowSimulationRunner(
            simulation_ledger=stale_sports,
            ledger_writer=SimulationPortfolioLedgerRepository().merge,
            liquidity_reserver=_ContendingReserver(
                start=start,
                loser_decided=loser_decided,
            ),
        )
        requests = {
            "bonus": _request(
                SimulationEngine.BONUS,
                bonus_request(
                    "concurrent-bonus",
                    generated_at=now,
                    back_stake=Decimal("10"),
                ),
                correlation_id=uuid4(),
            ),
            "sports": _request(
                SimulationEngine.SPORTS_CAPITAL,
                _sports_request(now),
                correlation_id=uuid4(),
            ),
        }
        runners = {"bonus": bonus_runner, "sports": sports_runner}
        results = {}
        errors: list[BaseException] = []

        def execute(name: str) -> None:
            close_old_connections()
            try:
                results[name] = runners[name].run(requests[name])
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        workers = (
            Thread(target=execute, args=("bonus",)),
            Thread(target=execute, args=("sports",)),
        )
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {"bonus", "sports"})

        workflow_results = [
            result.workflow_results[0]
            for result in results.values()
        ]
        rejected = [
            result
            for result in workflow_results
            if result.final_decision is WorkflowDecision.REJECT
        ]
        allowed = [
            result
            for result in workflow_results
            if result.final_decision is WorkflowDecision.ALLOW
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(len(allowed), 1)

        liquidity_transition = next(
            transition
            for transition in rejected[0].transitions
            if transition.stage is WorkflowStage.LIQUIDITY_CHECK
        )
        self.assertEqual(
            liquidity_transition.reason,
            RejectionReason.CAPITAL_LIMIT.value,
        )

        persisted = SimulationPortfolioLedgerRepository().load(currency="EUR")
        assert persisted is not None
        self.assertGreaterEqual(len(persisted.commands), 4)

        allowed_run = next(
            run
            for run in results.values()
            if run.workflow_results[0].final_decision is WorkflowDecision.ALLOW
        )
        rejected_run = next(
            run
            for run in results.values()
            if run.workflow_results[0].final_decision is WorkflowDecision.REJECT
        )
        persisted_correlations = {
            command.correlation_id for command in persisted.commands.values()
        }
        self.assertIn(str(allowed_run.correlation_id), persisted_correlations)
        self.assertNotIn(str(rejected_run.correlation_id), persisted_correlations)
        self.assertEqual(rejected_run.simulation_result.completed_steps, ())
