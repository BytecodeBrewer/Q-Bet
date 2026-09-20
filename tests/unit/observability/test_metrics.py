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
