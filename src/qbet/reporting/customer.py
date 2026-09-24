"""Business-only report projection for customer-facing result documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from qbet.reporting.models import (
    CustomerReportAmount,
    CustomerReportFinancialTerm,
    SimulationReport,
)
from qbet.simulation.models import SimulationStatus


class CustomerReportUnavailable(ValueError):
    """Raised when a persisted result cannot truthfully become a customer report."""


@dataclass(frozen=True)
class CustomerResultReport:
    """Stable business projection with no pipeline or monitoring implementation details."""

    report_id: UUID
    mode: Literal["simulation"]
    match: str
    provider: str
    counterparty_provider: str
    engine: str
    strategy: str
    assigned_amounts: tuple[CustomerReportAmount, ...]
    financial_terms: tuple[CustomerReportFinancialTerm, ...]
    invested_capital: Decimal
    result_state: str
    profit_loss: Decimal
    current_capital: Decimal
    currency: str
    created_at: datetime
    completed_at: datetime
    transaction_id: str | None

    @classmethod
    def from_simulation_report(cls, report: SimulationReport) -> CustomerResultReport:
        if report.status is not SimulationStatus.COMPLETED:
            raise CustomerReportUnavailable(
                "This result is not completed and cannot be reported yet."
            )
        if report.customer_report_input is None:
            raise CustomerReportUnavailable(
                "Required business data is unavailable, so no customer report was created."
            )

        business = report.customer_report_input
        return cls(
            report_id=report.run_id,
            mode="simulation",
            match=business.match,
            provider=business.provider,
            counterparty_provider=business.counterparty_provider,
            engine=str(report.engine),
            strategy=business.strategy,
            assigned_amounts=business.assigned_amounts,
            financial_terms=business.financial_terms,
            invested_capital=business.invested_capital,
            result_state=business.result_state,
            profit_loss=report.profit_loss,
            current_capital=report.current_capital,
            currency=business.currency,
            created_at=report.generated_at,
            completed_at=report.generated_at,
            transaction_id=str(business.transaction_id) if business.transaction_id else None,
        )
