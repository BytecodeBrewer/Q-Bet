"""Controlled Prometheus-compatible exposition for Q-Bet operational telemetry."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.utils import timezone

from qbet.monitoring.prometheus import render_prometheus_metrics
from qbet.monitoring.service import MonitoringQuery
from qbet.storage.models import ModeWorkQueueRow
from qbet.storage.monitoring import MonitoringPersistenceError, PostgresMonitoringRepository

_METRICS_WINDOW = timedelta(minutes=15)


def prometheus_metrics(request: HttpRequest) -> HttpResponse:
    """Expose bounded operational telemetry when explicitly enabled for the deployment."""

    del request
    if not bool(getattr(settings, "QBET_METRICS_ENABLED", False)):
        return HttpResponseNotFound()

    end = timezone.now()
    query = MonitoringQuery(start=end - _METRICS_WINDOW, end=end)
    try:
        records = PostgresMonitoringRepository().list_records(query)
        queue_rows = ModeWorkQueueRow.objects.values("mode", "state").annotate(total=Count("work_id"))
        queue_counts = {
            (str(row["mode"]), str(row["state"])): int(row["total"])
            for row in queue_rows
        }
    except (MonitoringPersistenceError, DatabaseError):
        return HttpResponse(
            "# qbet metrics temporarily unavailable\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    return HttpResponse(
        render_prometheus_metrics(records, queue_counts),
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )
