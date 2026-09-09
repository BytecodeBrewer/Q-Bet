from datetime import UTC, datetime, timedelta

from qbet.monitoring import MonitoringQuery, MonitoringRecord, MonitoringService


class _UnavailableReader:
    def list_records(self, query: MonitoringQuery) -> tuple[MonitoringRecord, ...]:
        del query
        raise OSError("monitoring history is unavailable")


def test_monitoring_service_marks_persistence_unavailability_without_hiding_it_as_empty() -> None:
    start = datetime(2026, 9, 9, 10, tzinfo=UTC)
    query = MonitoringQuery(start=start, end=start + timedelta(hours=1))
    service = MonitoringService(_UnavailableReader())

    extended = service.extended(query)
    compact = service.compact(query)

    assert extended == ()
    assert compact == ()
    assert extended.available is False
    assert compact.available is False
    assert extended.message == "Monitoring history is temporarily unavailable."
    assert compact.message == "Monitoring history is temporarily unavailable."
