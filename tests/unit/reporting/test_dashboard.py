from datetime import UTC, datetime
from decimal import Decimal

from qbet.reporting.dashboard import CustomerReportingQuery, CustomerReportingService

from .test_customer_reporting import _customer_input, _simulation_report


class Reader:
    def __init__(self, reports):
        self.reports = tuple(reports)

    def list_recent_reports(self, limit=20):
        return self.reports[:limit]


def test_dashboard_filters_completed_customer_results_and_calculates_roi() -> None:
    source = _simulation_report(customer_input=_customer_input())
    dashboard = CustomerReportingService(Reader((source,))).dashboard(
        CustomerReportingQuery(
            start=datetime(2026, 9, 5, 0, tzinfo=UTC),
            end=datetime(2026, 9, 6, 0, tzinfo=UTC),
            engine="bonus",
            mode="simulation",
        )
    )

    assert dashboard.completed_count == 1
    assert dashboard.invested_capital == Decimal("50")
    assert dashboard.profit_loss == Decimal("12.50")
    assert dashboard.roi == Decimal("25.00")


def test_dashboard_rejects_unbounded_time_ranges() -> None:
    try:
        CustomerReportingQuery(
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 10, 3, tzinfo=UTC),
        )
    except ValueError as error:
        assert "31 days" in str(error)
    else:
        raise AssertionError("expected bounded reporting range validation")


def test_dashboard_excludes_unowned_reports_before_calculating_kpis() -> None:
    owned = _simulation_report(customer_input=_customer_input())
    unowned = _simulation_report(customer_input=_customer_input())

    dashboard = CustomerReportingService(Reader((owned, unowned))).dashboard(
        CustomerReportingQuery(
            start=datetime(2026, 9, 5, 0, tzinfo=UTC),
            end=datetime(2026, 9, 6, 0, tzinfo=UTC),
            report_ids=frozenset((owned.run_id,)),
        )
    )

    assert tuple(report.report_id for report in dashboard.reports) == (owned.run_id,)
    assert dashboard.completed_count == 1
    assert dashboard.invested_capital == Decimal("50")
    assert dashboard.profit_loss == Decimal("12.50")
