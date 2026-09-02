"""Server-rendered views for the Q-Bet web shell."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from qbet.storage import SQLiteSimulationReportReader
from qbet.web.forms import RegistrationForm
from qbet.web.monitoring import MonitoringService


def _monitoring_service() -> MonitoringService:
    database_path = settings.QBET_SIMULATION_REPORT_DB
    if database_path is None:
        return MonitoringService()
    return MonitoringService(SQLiteSimulationReportReader(database_path))


MONITORING_SERVICE = _monitoring_service()


def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "qbet_web/home.html", {"monitoring": MONITORING_SERVICE.snapshot()})


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your Q-Bet account is ready.")
        return redirect("dashboard")
    return render(request, "qbet_web/register.html", {"form": form})


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(
        request, "qbet_web/dashboard.html", {"monitoring": MONITORING_SERVICE.snapshot()}
    )


def monitoring(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/monitoring.html",
        {"monitoring": MONITORING_SERVICE.snapshot()},
    )


@user_passes_test(lambda user: user.is_staff, login_url="login")
def admin_area(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/admin_area.html")


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
