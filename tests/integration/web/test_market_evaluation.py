from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from qbet.storage.ledger import RoutingConfigurationRepository, UserRoutingPreferenceRepository
from qbet.storage.models import (
    ExecutionRecordRow,
    MarketEvaluationRow,
    PortfolioLedgerRow,
    SimulationReportRow,
)
from qbet.storage.polling import PollingStrategyRepository
from qbet.storage.polling_work import PostgresPollingWorkRepository
from qbet.data.polling import PollingStrategy, PollingTarget
from qbet.web.models import SimulationRunState
from qbet.web.polling_tick import configured_polling_work
from qbet.web.simulation_control import SimulationControlError, SimulationControlService
from qbet.web.market_evaluation import consume_snapshots
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)
from tests.integration.web.test_polling_tick import RecordingCollector
from tests.integration.web import test_bonus_offer_simulation as bonus_fixtures


class FreshCollector(RecordingCollector):
    def collect(self, request):
        snapshot = super().collect(request)
        return snapshot.model_copy(
            update={
                "offers": tuple(
                    offer.model_copy(update={"source_updated_at": snapshot.fetched_at})
                    for offer in snapshot.offers
                )
            }
        )


class MarketEvaluationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="sandbox-admin", is_staff=True)
        self.second = User.objects.create_user(username="second-admin", is_staff=True)
        self.config = self.settings(
            QBET_POLLING_TICK_TOKEN="test",
            QBET_SIMULATION_SPORTS_SOURCE="the_odds_api",
            QBET_SIMULATION_ODDS_SPORT="soccer_germany_bundesliga",
            QBET_SIMULATION_ODDS_EVENT_ID="match",
            QBET_SIMULATION_ODDS_MARKET="h2h",
            QBET_SIMULATION_ODDS_EVENT_STARTS_AT=(
                datetime.now(UTC) + timedelta(hours=2)
            ).isoformat(),
            QBET_SIMULATION_ASSUMED_LIQUIDITY="100",
            QBET_SIMULATION_REQUESTED_TOTAL_STAKE="20",
            QBET_SIMULATION_STAKE_PRECISION="0.01",
        )
        self.config.enable()
        self.addCleanup(self.config.disable)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(
                sports_capital=EngineModes(simulation=True),
            )
        )
        for staff in (self.staff, self.second):
            UserRoutingPreferenceRepository().save(
                str(staff.pk),
                UserRoutingPreferences(
                    sports_capital=UserEngineModes(simulation=True),
                ),
            )
        self.now = datetime.now(UTC)
        # Persist a due target; the real endpoint performs collection and dispatch.
        from qbet.data import DataSourceMetadata, SourceTransport

        source = DataSourceMetadata(
            provider_id="the_odds_api",
            source_id="simulation-the-odds-api",
            transport=SourceTransport.API,
        )
        PollingStrategyRepository().save(
            PollingStrategy(
                source=source,
                target=PollingTarget.MARKET,
                engine="sports_capital",
                market_interval=timedelta(minutes=1),
                freshness_window=timedelta(minutes=5),
            )
        )
        self.work = configured_polling_work(now=self.now)[0].model_copy(
            update={
                "next_due_at": self.now - timedelta(minutes=2),
                "last_outcome": "scheduled",
                "last_reason": "market_refresh_due",
            }
        )
        PostgresPollingWorkRepository().synchronize((self.work,))
        SimulationControlService().set_enabled(True)
        SimulationControlService().seed_portfolio(amount=Decimal("100"), currency="EUR")

    def tick(self, collector):
        with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
            return self.client.post("/internal/polling/tick/", HTTP_AUTHORIZATION="Bearer test")

    def test_real_tick_shared_admin_report_and_duplicate_wake(self):
        collector = FreshCollector()
        self.assertEqual(self.tick(collector).status_code, 200)
        row = MarketEvaluationRow.objects.get()
        self.assertEqual(row.outcome, "simulated", row.reason)
        self.assertEqual(SimulationRunState.objects.get().report_id, row.run_id)
        self.assertEqual(SimulationReportRow.objects.count(), 1)
        ledger = PortfolioLedgerRow.objects.get(mode="simulation").payload
        self.assertEqual(self.tick(collector).status_code, 200)
        self.assertEqual(len(collector.requests), 1)
        self.assertEqual(SimulationRunState.objects.count(), 1)
        self.assertEqual(PortfolioLedgerRow.objects.get(mode="simulation").payload, ledger)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        # Both administrators can inspect the single shared lifecycle.
        self.assertEqual(len(SimulationControlService().snapshot().runs), 1)

    def test_failure_after_ledger_writes_rolls_back_and_restart_recovers(self):
        original = SimulationControlService.run
        initial = PortfolioLedgerRow.objects.get(mode="simulation").payload

        def fail(service, run_id):
            original(service, run_id)
            raise SimulationControlError("worker interrupted")

        with patch.object(SimulationControlService, "run", fail):
            self.assertEqual(self.tick(FreshCollector()).status_code, 200)
        row = MarketEvaluationRow.objects.get()
        self.assertEqual(row.outcome, "retry")
        self.assertEqual(SimulationRunState.objects.count(), 0)
        self.assertEqual(SimulationReportRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.get(mode="simulation").payload, initial)
        self.assertEqual(
            consume_snapshots(
                active_work=(self.work,), now=self.now + timedelta(seconds=40), limit=10
            ),
            1,
        )
        row.refresh_from_db()
        self.assertEqual(row.outcome, "simulated", row.reason)

    def test_same_snapshot_saved_twice_does_not_duplicate_and_transaction_rollback(self):
        snapshot = FreshCollector().collect(self.work.collection_request())
        work = self.work.model_copy(update={"latest_snapshot": snapshot})
        repo = PostgresPollingWorkRepository()
        with self.assertRaises(RuntimeError), transaction.atomic():
            repo.save(work)
            raise RuntimeError("crash before commit")
        self.assertEqual(MarketEvaluationRow.objects.count(), 0)
        repo.save(work)
        repo.save(work.model_copy(update={"attempt": 2}))
        self.assertEqual(MarketEvaluationRow.objects.count(), 1)

    def test_real_worker_error_is_retryable_after_rollback(self):
        from qbet.simulation.workflow import WorkflowSimulationRunner

        original = WorkflowSimulationRunner.run
        initial = PortfolioLedgerRow.objects.get(mode="simulation").payload

        def interrupted(runner, *args, **kwargs):
            original(runner, *args, **kwargs)
            raise RuntimeError("transient worker failure after ledger writes")

        with patch.object(WorkflowSimulationRunner, "run", interrupted):
            self.assertEqual(self.tick(FreshCollector()).status_code, 200)
        row = MarketEvaluationRow.objects.get()
        self.assertEqual((row.outcome, row.reason), ("retry", "simulation_failed"))
        self.assertEqual(SimulationRunState.objects.count(), 0)
        self.assertEqual(SimulationReportRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.get(mode="simulation").payload, initial)
        self.assertEqual(consume_snapshots(
            active_work=(self.work,), now=self.now + timedelta(seconds=40), limit=10
        ), 1)
        row.refresh_from_db()
        self.assertEqual(row.outcome, "simulated", row.reason)
        ledger = PortfolioLedgerRow.objects.get(mode="simulation").payload
        self.assertEqual(consume_snapshots(
            active_work=(self.work,), now=self.now + timedelta(seconds=41), limit=10
        ), 0)
        self.assertEqual(SimulationReportRow.objects.count(), 1)
        self.assertEqual(PortfolioLedgerRow.objects.get(mode="simulation").payload, ledger)

    def test_stale_snapshot_and_execution_selection_never_dispatch(self):
        snapshot = FreshCollector().collect(self.work.collection_request())
        PostgresPollingWorkRepository().save(
            self.work.model_copy(update={"latest_snapshot": snapshot})
        )
        consume_snapshots(active_work=(self.work,), now=self.now + timedelta(minutes=6), limit=10)
        self.assertEqual(MarketEvaluationRow.objects.get().outcome, "missing_input")
        self.assertEqual(SimulationRunState.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)

    def test_customer_preferences_do_not_authorize_admin_sandbox(self):
        User.objects.filter(pk__in=(self.staff.pk, self.second.pk)).update(is_staff=False)
        self.assertEqual(self.tick(FreshCollector()).status_code, 200)
        self.assertEqual(MarketEvaluationRow.objects.get().outcome, "unevaluated")
        self.assertEqual(SimulationRunState.objects.count(), 0)

    def test_two_users_with_both_modes_share_only_simulation(self):
        RoutingConfigurationRepository().save(
            RoutingConfiguration(
                sports_capital=EngineModes(simulation=True, execution=True),
            )
        )
        UserRoutingPreferenceRepository().save(
            str(self.second.pk),
            UserRoutingPreferences(
                sports_capital=UserEngineModes(simulation=False, execution=True),
            ),
        )
        self.assertEqual(self.tick(FreshCollector()).status_code, 200)
        self.assertEqual(MarketEvaluationRow.objects.get().outcome, "simulated")
        self.assertEqual(SimulationReportRow.objects.count(), 1)


        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertFalse(PortfolioLedgerRow.objects.filter(mode="execution").exists())

    def test_configuration_change_supersedes_pending_snapshot(self):
        snapshot = FreshCollector().collect(self.work.collection_request())
        PostgresPollingWorkRepository().save(
            self.work.model_copy(update={"latest_snapshot": snapshot})
        )
        with self.settings(QBET_SIMULATION_REQUESTED_TOTAL_STAKE="30"):
            consume_snapshots(
                active_work=(self.work,), now=self.now + timedelta(seconds=1), limit=10
            )
        self.assertEqual(MarketEvaluationRow.objects.get().outcome, "superseded")
        self.assertEqual(SimulationRunState.objects.count(), 0)

    def test_valid_unprofitable_is_distinct_from_missing_input(self):
        class UnprofitableCollector(FreshCollector):
            def collect(self, request):
                snapshot = super().collect(request)
                return snapshot.model_copy(
                    update={
                        "offers": tuple(
                            offer.model_copy(update={"odds": Decimal("1.90")})
                            for offer in snapshot.offers
                        )
                    }
                )

        self.assertEqual(self.tick(UnprofitableCollector()).status_code, 200)
        self.assertEqual(MarketEvaluationRow.objects.get().outcome, "unprofitable")
        self.assertEqual(SimulationReportRow.objects.count(), 1)


@override_settings(
    QBET_SIMULATION_ASSUMED_LIQUIDITY="100",
    QBET_SIMULATION_STAKE_PRECISION="0.01",
    QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS={
    "tipico": {"fee_rate": "0", "tax_mode": "none"},
    "winamax": {"fee_rate": "0", "tax_mode": "none"},
})
class BonusMarketEvaluationTests(TestCase):
    setUp = bonus_fixtures.BonusOfferSimulationTests.setUp
    _qualifying_offer = bonus_fixtures.BonusOfferSimulationTests._qualifying_offer

    def consume_bonus(self):
        from qbet.data.polling_runtime import PollingWork
        from qbet.web.market_evaluation import enqueue_snapshot

        now = datetime.now(UTC)
        snapshot = bonus_fixtures._snapshot()
        snapshot = snapshot.model_copy(update={"offers": tuple(
            offer.model_copy(update={"source_updated_at": now}) for offer in snapshot.offers
        )})
        work = PollingWork(
            correlation_id=snapshot.correlation_id,
            engine="bonus", mode="simulation", sport=snapshot.sport,
            match_id=snapshot.event_id, market="h2h", source=snapshot.source,
            target=PollingTarget.MARKET, next_due_at=now,
            event_starts_at=now + timedelta(hours=2), latest_snapshot=snapshot,
        )
        RoutingConfigurationRepository().save(RoutingConfiguration(
            bonus=EngineModes(simulation=True),
        ))
        SimulationControlService().set_enabled(True)
        PollingStrategyRepository().save(PollingStrategy(
            source=work.source, target=PollingTarget.MARKET, engine="bonus",
            market_interval=timedelta(minutes=1), freshness_window=timedelta(minutes=5),
        ))
        enqueue_snapshot(work)
        self.assertEqual(consume_snapshots(active_work=(work,), now=now + timedelta(seconds=1), limit=10), 1)
        row = MarketEvaluationRow.objects.get()
        self.assertEqual(row.outcome, "simulated", row.reason)
        self.assertEqual(SimulationRunState.objects.get().initiated_by_id, self.other.pk)
        self.assertEqual(SimulationReportRow.objects.count(), 1)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)

    def enable_owner(self, user, enabled=True):
        UserRoutingPreferenceRepository().save(str(user.pk), UserRoutingPreferences(
            bonus=UserEngineModes(simulation=enabled),
        ))

    def test_bonus_owner_opt_out_is_respected(self):
        self._qualifying_offer(self.user)
        self._qualifying_offer(self.other)
        self.enable_owner(self.user, False)
        self.enable_owner(self.other)
        self.consume_bonus()


    def test_expired_first_owner_does_not_hide_eligible_offer(self):
        first = self._qualifying_offer(self.user)
        first.valid_until = datetime.now(UTC) - timedelta(days=1)
        first.save()
        self._qualifying_offer(self.other)
        self.enable_owner(self.user)
        self.enable_owner(self.other)
        self.consume_bonus()

    def test_incompatible_first_owner_does_not_hide_eligible_offer(self):
        first = self._qualifying_offer(self.user)
        first.minimum_odds = Decimal("9")
        first.save()
        self._qualifying_offer(self.other)
        self.enable_owner(self.user)
        self.enable_owner(self.other)
        self.consume_bonus()

    def test_unsupported_first_offer_does_not_hide_eligible_offer(self):
        first = self._qualifying_offer(self.user)
        first.wagering_requirement = Decimal("5")
        first.save()
        self._qualifying_offer(self.other)
        self.enable_owner(self.user)
        self.enable_owner(self.other)
        self.consume_bonus()


class MarketEvaluationClaimTests(TransactionTestCase):
    setUp = MarketEvaluationTests.setUp

    def test_locked_claim_is_skipped_then_processed_once(self):
        snapshot = FreshCollector().collect(self.work.collection_request())
        PostgresPollingWorkRepository().save(self.work.model_copy(update={"latest_snapshot": snapshot}))
        row = MarketEvaluationRow.objects.get()
        locked, release = Event(), Event()

        def hold_claim():
            close_old_connections()
            try:
                with transaction.atomic():
                    MarketEvaluationRow.objects.select_for_update().get(pk=row.pk)
                    locked.set()
                    release.wait(timeout=15)
            finally:
                close_old_connections()

        worker = Thread(target=hold_claim)
        worker.start()
        try:
            self.assertTrue(locked.wait(timeout=5))
            self.assertEqual(consume_snapshots(
                active_work=(self.work,), now=self.now + timedelta(seconds=1), limit=10
            ), 0)
            self.assertEqual(SimulationRunState.objects.count(), 0)
        finally:
            release.set()
            worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(consume_snapshots(
            active_work=(self.work,), now=self.now + timedelta(seconds=2), limit=10
        ), 1)
        self.assertEqual(consume_snapshots(
            active_work=(self.work,), now=self.now + timedelta(seconds=3), limit=10
        ), 0)
        self.assertEqual(SimulationReportRow.objects.count(), 1)
