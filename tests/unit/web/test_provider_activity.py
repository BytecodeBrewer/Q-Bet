from datetime import UTC, datetime, timedelta
from uuid import UUID

from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.web.provider_activity import provider_activity_snapshot

NOW = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def record(
    *,
    status: str,
    occurred_at: datetime = NOW,
    reason_code: str | None = None,
) -> MonitoringRecord:
    return MonitoringRecord(
        correlation_id=CORRELATION_ID,
        occurred_at=occurred_at,
        engine="sports_capital",
        mode="simulation",
        stage="data_aggregation",
        event_type="provider_query",
        status=status,
        reason_code=reason_code,
        level=MonitoringLevel.INFO,
        duration_ms=12,
        references={
            "provider_id": "the-odds-api",
            "source_id": "simulation-the-odds-api",
        },
    )


def test_no_recent_provider_query_is_ready_not_success() -> None:
    snapshot = provider_activity_snapshot((), now=NOW)

    assert snapshot.state == "ready"
    assert snapshot.label == "Market data ready; no query running."


def test_current_provider_query_is_working() -> None:
    snapshot = provider_activity_snapshot((record(status="working"),), now=NOW)

    assert snapshot.state == "working"
    assert snapshot.is_working
    assert snapshot.provider == "the-odds-api"


def test_stale_working_query_becomes_delayed() -> None:
    snapshot = provider_activity_snapshot(
        (record(status="working", occurred_at=NOW - timedelta(minutes=2)),),
        now=NOW,
    )

    assert snapshot.state == "delayed"


def test_success_and_error_states_remain_truthful() -> None:
    success = provider_activity_snapshot((record(status="success"),), now=NOW)
    failure = provider_activity_snapshot((record(status="error"),), now=NOW)

    assert success.state == "success"
    assert failure.state == "error"


def test_unavailable_history_fails_closed_without_fake_green() -> None:
    snapshot = provider_activity_snapshot((), now=NOW, available=False)

    assert snapshot.state == "unavailable"
    assert "unavailable" in snapshot.label.lower()
