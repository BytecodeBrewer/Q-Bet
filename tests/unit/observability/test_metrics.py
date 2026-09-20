import json
from pathlib import Path

from qbet.observability.metrics import (
    ObservabilitySnapshot,
    aggregate_monitoring,
    prometheus_document,
)


def test_aggregate_monitoring_restricts_dynamic_values_to_other() -> None:
    events, durations = aggregate_monitoring(
        (
            ("sports_capital", "execution", "warning", 25),
            ("customer-123", "sandbox", "secret", 10),
        )
    )

    assert events == {
        ("sports_capital", "execution", "warning"): 1,
        ("other", "other", "other"): 1,
    }
    assert durations == {
        ("sports_capital", "execution"): (1, 25),
        ("other", "other"): (1, 10),
    }


def test_prometheus_document_contains_only_aggregate_labels() -> None:
    document = prometheus_document(
        ObservabilitySnapshot(
            available=True,
            monitoring_events={("bonus", "simulation", "info"): 2},
            monitoring_durations={("bonus", "simulation"): (1, 16)},
            queue_items={("execution", "pending"): 3},
            execution_records={("execution", "awaiting_approval"): 1},
        )
    ).decode()

    assert "qbet_observability_source_available 1" in document
    assert 'engine="bonus",mode="simulation",level="info"' in document
    assert "correlation" not in document
    assert "opportunity" not in document
    assert "token" not in document


def test_prometheus_types_match_cumulative_dashboard_rate_queries() -> None:
    document = prometheus_document(
        ObservabilitySnapshot(
            available=True,
            monitoring_events={("bonus", "simulation", "warning"): 2},
            monitoring_durations={("bonus", "simulation"): (2, 40)},
            queue_items={},
            execution_records={},
            monitoring_failures={("bonus", "simulation", "failure"): 1},
        )
    ).decode()
    dashboard = json.loads(
        Path("dashboards/qbet-observability.json").read_text(encoding="utf-8")
    )
    expressions = " ".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", ())
    )

    assert "# TYPE qbet_monitoring_events_total counter" in document
    assert "# TYPE qbet_monitoring_failures_total counter" in document
    assert "# TYPE qbet_monitoring_duration_milliseconds summary" in document
    assert "rate(qbet_monitoring_events_total[5m])" in expressions
    assert "rate(qbet_monitoring_duration_milliseconds_sum[5m])" in expressions
    assert "rate(qbet_monitoring_duration_milliseconds_count[5m])" in expressions


def test_grafana_dashboard_covers_the_exported_metric_contract() -> None:
    dashboard = json.loads(
        Path("dashboards/qbet-observability.json").read_text(encoding="utf-8")
    )
    expressions = " ".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", ())
    )

    for metric in (
        "qbet_monitoring_events_total",
        "qbet_queue_items",
        "qbet_execution_records",
        "qbet_monitoring_duration_milliseconds_sum",
        "qbet_monitoring_duration_milliseconds_count",
        "qbet_monitoring_latest_event_timestamp_seconds",
        "qbet_observability_source_available",
    ):
        assert metric in expressions

    datasource_variables = [
        item
        for item in dashboard["templating"]["list"]
        if item.get("type") == "datasource"
    ]
    assert datasource_variables
    assert datasource_variables[0]["query"] == "prometheus"
    assert all(
        panel.get("datasource", {}).get("uid") == "${datasource}"
        for panel in dashboard["panels"]
    )
