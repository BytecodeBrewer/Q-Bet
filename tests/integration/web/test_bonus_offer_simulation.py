from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from qbet.calculations import SportsbookFreeBetInput
from qbet.data import (
    CompletenessStatus,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
)
from qbet.domain.models import OfferSide
from qbet.domain.verification import ProviderState
from qbet.providers import ProviderStatus, load_german_sportsbook_catalog
from qbet.layers import SimulationLogRecordType
from qbet.simulation import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import SimulationOpportunitySourceError
from qbet.storage.models import ExecutionRecordRow, SportsbookProviderRow
from qbet.storage.postgres import (
    PostgresProviderStateRepository,
    PostgresSimulationReportReader,
)
from qbet.storage.providers import PostgresSportsbookCatalogRepository
from qbet.web.bonus_offer_simulation import (
    BonusOfferSimulationConfig,
    BonusOfferSimulationOpportunitySource,
)
from qbet.web.models import BonusOffer, SimulationAvailability
from qbet.web.simulation_control import SimulationControlService
from qbet.workflow import WorkflowStage


class _Collector:
    def __init__(self, snapshot: NormalizedMarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def collect(self, request):
        self.calls += 1
        return self.snapshot.model_copy(update={"correlation_id": request.correlation_id})


def _snapshot(*, fresh: bool = True) -> NormalizedMarketSnapshot:
    now = timezone.now()
    market_id = "event-123:h2h"

    def item(identifier: str, selection: str, provider: str, odds: str) -> NormalizedOffer:
        return NormalizedOffer(
            id=identifier,
            market_id=market_id,
            selection=selection,
            provider=provider,
            side=OfferSide.BACK,
            odds=Decimal(odds),
            available_stake=Decimal("100"),
            currency="EUR",
            availability=OfferAvailability.AVAILABLE,
            observed_at=now,
        )

    return NormalizedMarketSnapshot(
        id=market_id,
        correlation_id=uuid4(),
        target=DataTarget.BONUS,
        source=DataSourceMetadata(
            provider_id=THE_ODDS_API_PROVIDER_ID,
            source_id="bonus-test-feed",
            transport=SourceTransport.API,
        ),
        sport="tennis_atp",
        event_id="event-123",
        market_id=market_id,
        fetched_at=now,
        freshness=FreshnessStatus.FRESH if fresh else FreshnessStatus.STALE,
        completeness=CompletenessStatus.COMPLETE,
        offers=(
            item("tipico_de:home", "home", "tipico_de", "2.50"),
            item("tipico_de:away", "away", "tipico_de", "1.80"),
            item("winamax_de:home", "home", "winamax_de", "2.20"),
            item("winamax_de:away", "away", "winamax_de", "2.50"),
        ),
    )


def _source(user: User, snapshot: NormalizedMarketSnapshot):
    return BonusOfferSimulationOpportunitySource(
        user_id=user.pk,
        config=BonusOfferSimulationConfig(
            sport="tennis_atp",
            event_id="event-123",
            market="h2h",
            assumed_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
        ),
        collector=_Collector(snapshot),
    )


@override_settings(
    QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS={
        "tipico": {"fee_rate": "0", "tax_mode": "none"},
        "winamax": {"fee_rate": "0", "tax_mode": "none"},
    }
)
class BonusOfferSimulationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "bonus-owner",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.other = User.objects.create_user(
            "other-owner",
            password="Strong-pass-123",
            is_staff=True,
        )
        PostgresSportsbookCatalogRepository().replace(load_german_sportsbook_catalog())
        self.tipico = SportsbookProviderRow.objects.get(provider_id="tipico")
        self.provider_states = PostgresProviderStateRepository()
        self.provider_states.upsert(
            ProviderState(provider_id="tipico", active_bets_count=0)
        )

    def _qualifying_offer(self, user: User | None = None) -> BonusOffer:
        return BonusOffer.objects.create(
            user=user or self.user,
            provider=self.tipico,
            name="API qualifier",
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            currency="EUR",
            required_stake=Decimal("10.00"),
            minimum_odds=Decimal("2.00"),
            valid_until=timezone.now() + timedelta(days=1),
        )

    def test_active_mapped_offer_is_preparation_ready(self) -> None:
        offer = self._qualifying_offer()

        self.assertTrue(offer.is_preparation_ready)
        self.assertEqual(offer.status_label, "Active")

    def test_user_owned_bonus_offer_runs_through_risk_liquidity_and_simulation(self) -> None:
        self._qualifying_offer()
        SimulationAvailability.objects.create(pk=1, enabled=True)
        source = _source(self.user, _snapshot())
        service = SimulationControlService(opportunity_source=source)

        run = service.start(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100"),
            initiated_by=self.user,
        )

        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.report_id)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        assert run.report_id is not None
        reader = PostgresSimulationReportReader()
        report = reader.load_report(run.report_id)
        records = reader.load_records(run.run_id)
        self.assertEqual(report.engine, SimulationEngine.BONUS.value)
        self.assertEqual(report.customer_report_input.provider, self.tipico.display_name)
        self.assertEqual(
            report.customer_report_input.invested_capital,
            Decimal("20.00"),
        )
        self.assertEqual(
            tuple(term.tax_mode for term in report.customer_report_input.financial_terms),
            ("none", "none"),
        )
        self.assertEqual(
            tuple(term.fee_rate for term in report.customer_report_input.financial_terms),
            (Decimal("0"), Decimal("0")),
        )
        stages = {
            record.payload.get("stage")
            for record in records
            if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        }
        self.assertIn(WorkflowStage.DOMAIN_RISK.value, stages)
        self.assertIn(WorkflowStage.LIQUIDITY_CHECK.value, stages)

    def test_persisted_provider_risk_state_rejects_connected_bonus_simulation(self) -> None:
        self._qualifying_offer()
        self.provider_states.upsert(
            ProviderState(provider_id="tipico", active_bets_count=2)
        )
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService(opportunity_source=_source(self.user, _snapshot()))

        run = service.start(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100"),
            initiated_by=self.user,
        )

        self.assertEqual(run.status, "stopped")
        self.assertEqual(run.current_capital, Decimal("100"))
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        assert run.report_id is not None
        records = PostgresSimulationReportReader().load_records(run.run_id)
        risk_records = tuple(
            record
            for record in records
            if record.record_type is SimulationLogRecordType.RISK_DECISION
        )
        self.assertTrue(risk_records)
        self.assertEqual(
            risk_records[-1].payload["decision_code"],
            "provider_frequency_limit",
        )

    @override_settings(
        QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS={
            "tipico": {"fee_rate": "0", "tax_mode": "none"},
        }
    )
    def test_missing_counterparty_financial_terms_fail_closed(self) -> None:
        self._qualifying_offer()
        source = _source(self.user, _snapshot())

        with self.assertRaises(SimulationOpportunitySourceError) as raised:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                uuid4(),
            )

        self.assertEqual(
            raised.exception.reason_code,
            "bonus_financial_terms_missing",
        )

    def test_free_bet_offer_uses_fixed_odds_sportsbook_input(self) -> None:
        BonusOffer.objects.create(
            user=self.user,
            provider=self.tipico,
            name="SNR free bet",
            promotion_type=BonusOffer.PromotionType.FREE_BET,
            promotion_value=Decimal("10.00"),
            currency="EUR",
            minimum_odds=Decimal("2.00"),
            stake_return_rule=BonusOffer.StakeReturnRule.STAKE_NOT_RETURNED,
            valid_until=timezone.now() + timedelta(days=1),
        )
        source = _source(self.user, _snapshot())

        bundle = source.build(
            SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100"),
            ),
            uuid4(),
        )

        self.assertEqual(len(bundle.opportunities), 1)
        request = bundle.opportunities[0]
        self.assertIsInstance(request.inputs, SportsbookFreeBetInput)
        self.assertEqual(
            bundle.customer_report_input.invested_capital,
            Decimal("6.00"),
        )
        self.assertEqual(
            tuple(
                (amount.label, amount.amount)
                for amount in bundle.customer_report_input.assigned_amounts
            ),
            (
                ("Promotion amount", Decimal("10.00")),
                ("Cash hedge stake", Decimal("6.00")),
            ),
        )

    def test_missing_provider_state_fails_closed_before_market_calculation(self) -> None:
        self._qualifying_offer()
        from qbet.storage.models import ProviderStateRow

        ProviderStateRow.objects.filter(provider_id="tipico").delete()
        source = _source(self.user, _snapshot())

        with self.assertRaises(SimulationOpportunitySourceError) as raised:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                uuid4(),
            )

        self.assertEqual(
            raised.exception.reason_code,
            "bonus_provider_state_unavailable",
        )

    def test_ineligible_provider_offer_is_unavailable_and_fails_closed(self) -> None:
        offer = self._qualifying_offer()
        self.tipico.status = ProviderStatus.INACTIVE.value
        self.tipico.save(update_fields=("status",))

        offer.refresh_from_db()
        self.assertFalse(offer.is_preparation_ready)
        self.assertEqual(offer.status_label, "Unavailable")

        source = _source(self.user, _snapshot())
        with self.assertRaises(SimulationOpportunitySourceError) as raised:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                uuid4(),
            )

        self.assertEqual(raised.exception.reason_code, "bonus_offer_unavailable")

    def test_source_never_uses_another_users_offer(self) -> None:
        self._qualifying_offer(self.other)
        source = _source(self.user, _snapshot())

        with self.assertRaises(SimulationOpportunitySourceError) as raised:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                uuid4(),
            )

        self.assertEqual(raised.exception.reason_code, "bonus_offer_missing")

    def test_stale_market_data_fails_closed(self) -> None:
        self._qualifying_offer()
        source = _source(self.user, _snapshot(fresh=False))

        with self.assertRaises(SimulationOpportunitySourceError) as raised:
            source.build(
                SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                uuid4(),
            )

        self.assertEqual(raised.exception.reason_code, "bonus_market_not_ready")
