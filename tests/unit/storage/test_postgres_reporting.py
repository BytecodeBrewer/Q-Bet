from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from django.test import TestCase

from qbet.calculations import QualifyingBetInput, calculate_qualifying_bet
from qbet.domain.models import StrategyResult
from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    ReportingSimulationRunner,
    SimulationEngine,
    SimulationEvaluation,
    SimulationRunConfig,
    SimulationStep,
    SimulationTopUpEvent,
)
from qbet.storage.postgres import PostgresSimulationReportStore


def evaluated_step() -> SimulationStep:
    calculation = calculate_qualifying_bet(
        QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal(10),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal(100),
        )
    )
    worst_case = min(
        calculation.back_win_profit_loss,
        calculation.lay_win_profit_loss,
    )
    evaluation = SimulationEvaluation(
        strategy_result=StrategyResult(
            strategy="qualifying_bet",
            opportunity_id="opportunity-1",
            stake=calculation.back_stake,
            expected_profit=worst_case,
            currency="EUR",
            generated_at=datetime(2026, 8, 29, tzinfo=UTC),
        ),
        calculation_result=calculation,
        worst_case_profit_loss=worst_case,
        is_profitable=False,
    )
    return SimulationStep(
        id="evaluated-step",
        capital_change=worst_case,
        evaluation=evaluation,
    )


class PostgresSimulationReportStoreTests(TestCase):
    def test_store_rebuilds_selected_detail_after_new_adapter_instance(self) -> None:
        store = PostgresSimulationReportStore()
        runner = ReportingSimulationRunner(store)

        result = runner.run(
            SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100.10"),
            ),
            (evaluated_step(),),
        )

        assert runner.last_report is not None
        run_id = runner.last_report.run_id
        reopened_store = PostgresSimulationReportStore()
        compact_report = reopened_store.load_report(run_id)
        records = reopened_store.load_records(run_id)
        detailed_report = SimulationReportBuilder().rebuild(
            compact_report,
            records,
            ReportDetailSelection(
                include_events=True,
                include_intermediate_results=True,
                include_raw_inputs=True,
            ),
        )

        compact_json = compact_report.model_dump_json()
        self.assertNotIn("calculation_result", compact_json)
        self.assertNotIn("strategy_result", compact_json)
        self.assertEqual(compact_report.current_capital, result.current_capital)
        self.assertEqual(compact_report.status, result.status)
        self.assertEqual([record.sequence for record in records], list(range(1, len(records) + 1)))
        self.assertEqual(detailed_report.events[0].sequence, 1)
        self.assertEqual(detailed_report.intermediate_results, result.evaluations)
        self.assertEqual(detailed_report.raw_input_snapshots[0]["config"]["starting_capital"], "100.10")
        self.assertEqual(reopened_store.list_recent_reports(), (compact_report,))

    def test_store_raises_for_missing_run_id(self) -> None:
        store = PostgresSimulationReportStore()

        with pytest.raises(KeyError):
            store.load_report(uuid4())

        with pytest.raises(KeyError):
            store.load_records(uuid4())

    def test_round_trip_preserves_top_up_timeline_and_net_profit(self) -> None:
        store = PostgresSimulationReportStore()
        runner = ReportingSimulationRunner(store)

        runner.run(
            SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal(100),
                top_up_events=(SimulationTopUpEvent(after_completed_steps=1, amount=Decimal(10)),),
            ),
            (SimulationStep(id="flat", capital_change=Decimal(0)),),
        )

        assert runner.last_report is not None
        reopened_store = PostgresSimulationReportStore()
        report = reopened_store.load_report(runner.last_report.run_id)
        records = reopened_store.load_records(runner.last_report.run_id)
        transition_payloads = [
            record.payload
            for record in records
            if record.record_type is SimulationLogRecordType.CAPITAL_TRANSITION
        ]

        self.assertEqual(report.current_capital, Decimal(110))
        self.assertEqual(report.top_up_total, Decimal(10))
        self.assertEqual(report.profit_loss, Decimal(0))
        self.assertEqual(
            transition_payloads,
            [
                {
                    "movement_type": "step",
                    "step_id": "flat",
                    "capital_change": "0",
                    "current_capital": "100",
                },
                {
                    "movement_type": "top_up",
                    "capital_change": "10",
                    "current_capital": "110",
                },
            ],
        )

    def test_rebuild_restores_selected_risk_decisions(self) -> None:
        store = PostgresSimulationReportStore()
        runner = ReportingSimulationRunner(store)
        runner.run(
            SimulationRunConfig(engine=SimulationEngine.BONUS, starting_capital=Decimal(100)),
            (SimulationStep(id="flat", capital_change=Decimal(0)),),
        )

        assert runner.last_report is not None
        run_id = runner.last_report.run_id
        persisted_records = store.load_records(run_id)
        store.append_records(
            (
                SimulationLogRecord(
                    run_id=run_id,
                    sequence=len(persisted_records) + 1,
                    timestamp=datetime.now(UTC),
                    record_type=SimulationLogRecordType.RISK_DECISION,
                    source="layers.operational_risk",
                    payload={
                        "status": "warn",
                        "decision_code": "provider_active_bet_limit_approaching",
                        "warning_codes": ["provider_active_bet_limit_approaching"],
                    },
                ),
            )
        )

        reopened_store = PostgresSimulationReportStore()
        compact_report = reopened_store.load_report(run_id)
        detailed_report = SimulationReportBuilder().rebuild(
            compact_report,
            reopened_store.load_records(run_id),
            ReportDetailSelection(include_risk_decisions=True),
        )

        self.assertEqual(compact_report.risk_decisions, ())
        decision = detailed_report.risk_decisions[0]
        self.assertEqual(decision.run_id, run_id)
        self.assertEqual(
            decision.payload,
            {
                "status": "warn",
                "decision_code": "provider_active_bet_limit_approaching",
                "warning_codes": ["provider_active_bet_limit_approaching"],
            },
        )
