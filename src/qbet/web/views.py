"""Server-rendered, read-only control and monitoring views for Q-Bet."""

from __future__ import annotations

import csv
import json
from io import StringIO
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from qbet.reporting import ReportDetailSelection
from qbet.storage import SQLiteSimulationReportReader
from qbet.web.controls import (
    GuiFeatureControlStore,
    PresentationPreferences,
    presentation_preferences,
    save_presentation_preferences,
)
from qbet.web.forms import (
    PresentationSettingsForm,
    RegistrationForm,
    SimulationAvailabilityForm,
)
from qbet.web.monitoring import MonitoringService

_DETAIL_FIELDS = (
    "include_events",
    "include_intermediate_results",
    "include_raw_inputs",
    "include_warnings",
    "include_errors",
    "include_risk_decisions",
    "include_workflow_transitions",
)


def _monitoring_service() -> MonitoringService:
    database_path = settings.QBET_SIMULATION_REPORT_DB
    if database_path is None:
        return MonitoringService()
    return MonitoringService(SQLiteSimulationReportReader(database_path))


MONITORING_SERVICE = _monitoring_service()
GUI_FEATURES = GuiFeatureControlStore(
    simulation_enabled=getattr(settings, "QBET_SIMULATION_MODE_ENABLED", False)
)


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    values.setdefault("simulation_enabled", GUI_FEATURES.simulation_enabled)
    return values


def _selection(request: HttpRequest) -> ReportDetailSelection:
    return ReportDetailSelection(
        **{field: request.GET.get(field) == "1" for field in _DETAIL_FIELDS}
    )



def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(
        request,
        "qbet_web/home.html",
        _context(request, monitoring=MONITORING_SERVICE.snapshot()),
    )


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your Q-Bet account is ready.")
            return redirect("dashboard")
        return render(
            request,
            "qbet_web/register.html",
            _context(request, form=RegistrationForm(), registration_error=True),
        )

    return render(request, "qbet_web/register.html", _context(request, form=form))


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/dashboard.html",
        _context(request, monitoring=MONITORING_SERVICE.snapshot()),
    )


@login_required
def engine_detail(request: HttpRequest, engine_id: str) -> HttpResponse:
    snapshot = MONITORING_SERVICE.snapshot()
    engine = next(
        (candidate for candidate in snapshot.engines if candidate.engine_id == engine_id),
        None,
    )
    if engine is None:
        raise Http404("Engine not found.")
    reports = tuple(report for report in snapshot.reports if report.engine == engine_id)
    return render(
        request,
        "qbet_web/engine_detail.html",
        _context(request, monitoring=snapshot, engine=engine, reports=reports),
    )


@login_required
def simulation(request: HttpRequest) -> HttpResponse:
    if not GUI_FEATURES.simulation_enabled:
        raise Http404("Simulation visibility is disabled.")
    return render(
        request,
        "qbet_web/simulation.html",
        _context(request, monitoring=MONITORING_SERVICE.snapshot()),
    )


@login_required
def presentation_settings(request: HttpRequest) -> HttpResponse:
    preferences = presentation_preferences(request.session)
    form = PresentationSettingsForm(
        request.POST or None,
        initial={"theme": preferences.theme, "font_size": preferences.font_size},
    )
    if request.method == "POST" and form.is_valid():
        save_presentation_preferences(
            request.session,
            PresentationPreferences(
                theme=form.cleaned_data["theme"],
                font_size=form.cleaned_data["font_size"],
            ),
        )
        messages.success(request, "Presentation preferences updated.")
        return redirect("presentation-settings")
    return render(request, "qbet_web/settings.html", _context(request, form=form))


@login_required
def report_history(request: HttpRequest) -> HttpResponse:
    snapshot = MONITORING_SERVICE.snapshot()
    engine_id = request.GET.get("engine")
    reports = tuple(
        report
        for report in snapshot.reports
        if engine_id in (None, "") or report.engine == engine_id
    )
    return render(
        request,
        "qbet_web/report_history.html",
        _context(
            request,
            monitoring=snapshot,
            reports=reports,
            selected_engine=engine_id or "",
        ),
    )


@login_required
def report_detail(request: HttpRequest, run_id: UUID) -> HttpResponse:
    selection = _selection(request)
    lookup = MONITORING_SERVICE.load_report(run_id, selection)
    if lookup.report is None:
        return render(
            request,
            "qbet_web/report_unavailable.html",
            _context(request, message=lookup.message or "This report is not available."),
            status=404,
        )
    return render(
        request,
        "qbet_web/report_detail.html",
        _context(
            request,
            report=lookup.report,
            detail_selection=selection,
            detail_message=lookup.message,
        ),
    )


@login_required
def report_export(request: HttpRequest, run_id: UUID, export_format: str) -> HttpResponse:
    selection = _selection(request)
    lookup = MONITORING_SERVICE.load_report(run_id, selection)
    if lookup.report is None:
        raise Http404(lookup.message or "Report not found.")
    report = lookup.report
    if export_format == "json":
        response = JsonResponse(report.model_dump(mode="json"), json_dumps_params={"indent": 2})
        response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.json"'
        return response
    if export_format != "csv":
        raise Http404("Export format not found.")

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(("field", "value"))
    writer.writerows(
        (
            ("run_id", report.run_id),
            ("engine", report.engine),
            ("mode", "simulation"),
            ("status", report.status),
            ("progress", report.progress),
            ("starting_capital", report.starting_capital),
            ("current_capital", report.current_capital),
            ("profit_loss", report.profit_loss),
            ("completed_steps", len(report.completed_steps)),
            ("generated_at", report.generated_at.isoformat()),
        )
    )
    for field in _DETAIL_FIELDS:
        value = getattr(report, field.removeprefix("include_").replace("raw_inputs", "raw_input_snapshots"), None)
        if value:
            writer.writerow((field, json.dumps(value, default=str, sort_keys=True)))
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.csv"'
    return response


def monitoring(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/monitoring.html",
        _context(request, monitoring=MONITORING_SERVICE.snapshot()),
    )


@user_passes_test(lambda user: bool(getattr(user, "is_staff", False)), login_url="login")
def admin_area(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/admin_area.html", _context(request))


@user_passes_test(lambda user: bool(getattr(user, "is_staff", False)), login_url="login")
def admin_gui_settings(request: HttpRequest) -> HttpResponse:
    form = SimulationAvailabilityForm(
        request.POST or None,
        initial={"simulation_enabled": GUI_FEATURES.simulation_enabled},
    )
    if request.method == "POST" and form.is_valid():
        GUI_FEATURES.set_simulation_enabled(form.cleaned_data["simulation_enabled"])
        messages.success(request, "Simulation visibility updated for this application.")
        return redirect("admin-gui-settings")
    return render(
        request,
        "qbet_web/admin_gui_settings.html",
        _context(request, form=form),
    )


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")