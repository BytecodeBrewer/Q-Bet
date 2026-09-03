"""Server-rendered control and monitoring views for Q-Bet."""

from __future__ import annotations

import csv
from datetime import timedelta
from io import StringIO
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from qbet.reporting import ReportDetailSelection
from qbet.simulation import SimulationEngine
from qbet.storage import SQLiteSimulationReportReader
from qbet.web.controls import (
    PresentationPreferences,
    presentation_preferences,
    save_presentation_preferences,
)
from qbet.web.forms import (
    PresentationSettingsForm,
    RegistrationForm,
    SimulationAvailabilityForm,
    SimulationStartForm,
)
from qbet.web.monitoring import MonitoringService
from qbet.web.report_exports import SimulationReportExport
from qbet.web.simulation_control import (
    SimulationControlError,
    SimulationControlService,
    SimulationDisableBlockedError,
)

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
SIMULATION_CONTROL = SimulationControlService()


def _simulation_enabled() -> bool:
    return SIMULATION_CONTROL.availability().enabled


def _is_staff(user: object) -> bool:
    return bool(getattr(user, "is_staff", False))


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    if "simulation_enabled" not in values:
        values["simulation_enabled"] = (
            _simulation_enabled()
            if request.user.is_authenticated
            else bool(getattr(settings, "QBET_SIMULATION_MODE_ENABLED", False))
        )
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
@require_GET
def simulation(request: HttpRequest) -> HttpResponse:
    control = SIMULATION_CONTROL.snapshot()
    if not control.availability.enabled:
        raise Http404("Simulation visibility is disabled.")
    return render(
        request,
        "qbet_web/simulation.html",
        _context(
            request,
            monitoring=MONITORING_SERVICE.snapshot(),
            simulation_control=control,
            start_form=SimulationStartForm(),
            simulation_enabled=True,
        ),
    )


@login_required
@require_POST
def simulation_start(request: HttpRequest) -> HttpResponse:
    if not _simulation_enabled():
        messages.error(request, "Simulation is disabled by the administrator.")
        return redirect("dashboard")

    form = SimulationStartForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "qbet_web/simulation.html",
            _context(
                request,
                monitoring=MONITORING_SERVICE.snapshot(),
                simulation_control=SIMULATION_CONTROL.snapshot(),
                start_form=form,
                simulation_enabled=True,
            ),
            status=400,
        )

    try:
        run = SIMULATION_CONTROL.start(
            engine=SimulationEngine(form.cleaned_data["engine"]),
            starting_capital=form.cleaned_data["starting_capital"],
            max_duration=timedelta(minutes=form.cleaned_data["max_duration_minutes"]),
        )
    except SimulationControlError as error:
        messages.error(request, str(error))
        return redirect("simulation")

    if run.report_id is None:
        messages.error(request, "Simulation completed but its report is unavailable.")
        return redirect("simulation")

    messages.success(
        request,
        f"Simulation {run.run_id} finished with status {run.status}.",
    )
    return redirect("report-detail", run_id=run.report_id)


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
    export = SimulationReportExport.from_report(lookup.report, selection)
    if export_format == "json":
        response = JsonResponse(export.json_document(), json_dumps_params={"indent": 2})
        response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.json"'
        return response
    if export_format != "csv":
        raise Http404("Export format not found.")

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(("field", "value"))
    writer.writerows(export.csv_rows())
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.csv"'
    return response


@user_passes_test(_is_staff, login_url="login")
@require_GET
def monitoring(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/monitoring.html",
        _context(request, monitoring=MONITORING_SERVICE.snapshot()),
    )


@user_passes_test(_is_staff, login_url="login")
def admin_area(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/admin_area.html", _context(request))


@user_passes_test(_is_staff, login_url="login")
@require_GET
def admin_gui_settings(request: HttpRequest) -> HttpResponse:
    control = SIMULATION_CONTROL.snapshot()
    return render(
        request,
        "qbet_web/admin_gui_settings.html",
        _context(
            request,
            simulation_control=control,
            simulation_enabled=control.availability.enabled,
            simulation_toggle_form=SimulationAvailabilityForm(
                initial={"enabled": control.availability.enabled}
            ),
        ),
    )


@user_passes_test(_is_staff, login_url="login")
@require_POST
def admin_simulation_availability(request: HttpRequest) -> HttpResponse:
    form = SimulationAvailabilityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid simulation availability setting.")
        return redirect("admin-gui-settings")

    enabled = form.cleaned_data["enabled"]
    try:
        SIMULATION_CONTROL.set_enabled(enabled)
    except SimulationDisableBlockedError as error:
        messages.error(request, str(error))
    except SimulationControlError as error:
        messages.error(request, str(error))
    else:
        state = "enabled" if enabled else "disabled"
        messages.success(request, f"Simulation availability {state}.")
    return redirect("admin-gui-settings")


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
