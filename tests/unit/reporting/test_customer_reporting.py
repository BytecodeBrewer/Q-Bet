from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from qbet.reporting import (
    CustomerReportAmount,
    CustomerReportInput,
    CustomerReportUnavailable,
    CustomerResultReport,
    SimulationReport,
)
from qbet.reporting.exports import CustomerReportExport, json_bytes
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus


def _simulation_report(
    *,
    status: SimulationStatus = SimulationStatus.COMPLETED,
    customer_input: CustomerReportInput | None = None,
) -> SimulationReport:
    return SimulationReport(
        run_id=uuid4(),
        config=SimulationRunConfig(engine=SimulationEngine.BONUS, starting_capital=Decimal("100")),
        engine=SimulationEngine.BONUS,
        strategy_id="qualifying_bet",
        status=status,
        starting_capital=Decimal("100"),
        current_capital=Decimal("112.50"),
        top_up_total=Decimal("0"),
        profit_loss=Decimal("12.50"),
        completed_steps=(),
        elapsed_duration=timedelta(minutes=4),
        progress=Decimal("1"),
        generated_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        customer_report_input=customer_input,
    )


def _customer_input() -> CustomerReportInput:
    return CustomerReportInput(
        match="Northbridge v Riverside",
        provider="Bookmaker A",
        counterparty_provider="Exchange B",
        strategy="Qualifying bet",
        assigned_amounts=(
            CustomerReportAmount(label="Back stake", amount=Decimal("50")),
            CustomerReportAmount(label="Lay stake", amount=Decimal("48.25")),
        ),
        invested_capital=Decimal("50"),
        currency="EUR",
        transaction_id="result-2026-09-05-001",
    )


def test_completed_simulation_projects_a_business_only_customer_report() -> None:
    report = CustomerResultReport.from_simulation_report(
        _simulation_report(customer_input=_customer_input())
    )

    assert report.mode == "simulation"
    assert report.match == "Northbridge v Riverside"
    assert report.provider == "Bookmaker A"
    assert report.counterparty_provider == "Exchange B"
    assert report.strategy == "Qualifying bet"
    assert report.invested_capital == Decimal("50")
    assert report.profit_loss == Decimal("12.50")
    assert report.transaction_id == "result-2026-09-05-001"
    assert not hasattr(report, "events")
    assert not hasattr(report, "workflow_transitions")


@pytest.mark.parametrize(
    ("status", "customer_input"),
    ((SimulationStatus.RUNNING, _customer_input()), (SimulationStatus.COMPLETED, None)),
)
def test_incomplete_or_non_completed_results_are_not_customer_reportable(
    status: SimulationStatus, customer_input: CustomerReportInput | None
) -> None:
    with pytest.raises(CustomerReportUnavailable):
        CustomerResultReport.from_simulation_report(
            _simulation_report(status=status, customer_input=customer_input)
        )


def test_customer_report_input_round_trips_in_the_persisted_simulation_payload() -> None:
    source = _simulation_report(customer_input=_customer_input())

    restored = SimulationReport.model_validate_json(source.model_dump_json())

    assert restored.customer_report_input == source.customer_report_input
    assert CustomerResultReport.from_simulation_report(restored).provider == "Bookmaker A"


def test_customer_exports_share_business_data_and_exclude_technical_metadata() -> None:
    customer_report = CustomerResultReport.from_simulation_report(
        _simulation_report(customer_input=_customer_input())
    )
    export = CustomerReportExport(customer_report)

    document = export.json_document()
    csv_content = "\n".join(",".join(row) for row in export.csv_rows())
    pdf_content = export.pdf_document()

    assert document["mode"] == "simulation"
    assert document["providers"] == {"primary": "Bookmaker A", "counterparty": "Exchange B"}
    assert document["assigned_amounts"][0] == {"label": "Back stake", "amount": "50"}
    assert set(document).isdisjoint(
        {"events", "session_id", "pipeline_step", "correlation_id", "adapter", "logs"}
    )
    assert "workflow" not in csv_content.lower()
    assert "correlation" not in csv_content.lower()
    assert pdf_content.startswith(b"%PDF-1.4")
    assert b"Q-Bet Result Report" in pdf_content
    assert b"Northbridge v Riverside" in pdf_content
    assert b"Simulation result" in pdf_content
    assert json_bytes(export) == json_bytes(export)
