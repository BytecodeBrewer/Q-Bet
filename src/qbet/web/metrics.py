"""Controlled Prometheus-compatible exposition for Q-Bet operational telemetry."""

from __future__ import annotations

import secrets
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
_BEARER_PREFIX = "Bearer "


def _authorized(request: HttpRequest) -> bool:
    configured_token = str(getattr(settings, "QBET_METRICS_TOKEN", ""))
    authorization = request.headers.get("Authorization", "")
    if not configured_token or not authorization.startswith(_BEARER_PREFIX):
        return False
    presented_token = authorization.removeprefix(_BEARER_PREFIX)
    return secrets.compare_digest(presented_token, configured_token)


def prometheus_metrics(request: HttpRequest) -> HttpResponse:
    """Expose bounded telemetry only for an explicitly enabled authenticated scraper."""

    if not bool(getattr(settings, "QBET_METRICS_ENABLED", False)) or not _authorized(request):
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
