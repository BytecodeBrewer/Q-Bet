"""Server-rendered views for the minimal Q-Bet web shell."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from qbet.storage import SQLiteSimulationReportReader
from qbet.web.monitoring import MonitoringService


def _monitoring_service() -> MonitoringService:
    database_path = settings.QBET_SIMULATION_REPORT_DB
    if database_path is None:
        return MonitoringService()
    return MonitoringService(SQLiteSimulationReportReader(database_path))


MONITORING_SERVICE = _monitoring_service()


def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def monitoring(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/monitoring.html",
        {"monitoring": MONITORING_SERVICE.snapshot()},
    )


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/home.html", {"monitoring": MONITORING_SERVICE.snapshot()})


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
