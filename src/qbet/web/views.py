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
    DashboardLayout,
    PresentationPreferences,
    dashboard_layout,
    presentation_preferences,
    save_dashboard_widget_order,
    save_presentation_preferences,
)
from qbet.web.forms import (
    PresentationSettingsForm,
    RegistrationForm,
    SimulationAvailabilityForm,
    SimulationStartForm,
)
from qbet.web.monitoring import MonitoringEngineStatus, MonitoringService, execution_snapshot
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


def _require_staff(request: HttpRequest) -> None:
    if not request.user.is_staff:
        raise Http404("This admin-only resource is not available.")


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    if "simulation_enabled" not in values:
        values["simulation_enabled"] = bool(
            request.user.is_authenticated
            and request.user.is_staff
            and _simulation_enabled()
        )
    return values


def _selection(request: HttpRequest) -> ReportDetailSelection:
    return ReportDetailSelection(
        **{field: request.GET.get(field) == "1" for field in _DETAIL_FIELDS}
    )


def _ordered_engines(
    engines: tuple[MonitoringEngineStatus, ...],
    order: tuple[str, ...],
) -> tuple[MonitoringEngineStatus, ...]:
    by_id = {engine.engine_id: engine for engine in engines}
    return tuple(by_id[engine_id] for engine_id in order if engine_id in by_id)


def _dashboard_context(
    request: HttpRequest,
    *,
    start_form: SimulationStartForm | None = None,
) -> dict[str, object]:
    layout: DashboardLayout = dashboard_layout(request.session)
    execution = execution_snapshot()
    values: dict[str, object] = {
        "execution_monitoring": execution,
        "execution_engines": _ordered_engines(execution.engines, layout.execution),
        "dashboard_layout": layout,
        "simulation_enabled": False,
    }
    if request.user.is_staff and _simulation_enabled():
        simulation_monitoring = MONITORING_SERVICE.snapshot()
        values.update(
            simulation_enabled=True,
            simulation_monitoring=simulation_monitoring,
            simulation_engines=_ordered_engines(
                simulation_monitoring.engines,
                layout.simulation,
            ),
            simulation_control=SIMULATION_CONTROL.snapshot(),
            start_form=start_form or SimulationStartForm(),
        )
    return _context(request, **values)


def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(
        request,
        "qbet_web/home.html",
        _context(request, monitoring=execution_snapshot()),
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
        _dashboard_context(request),
    )


@login_required
@require_POST
def dashboard_layout_update(request: HttpRequest) -> JsonResponse:
    plane = request.POST.get("plane", "")
    if plane == "simulation" and (not request.user.is_staff or not _simulation_enabled()):
        raise Http404("Simulation dashboard is not available.")
    order = tuple(request.POST.getlist("order"))
    try:
        updated = save_dashboard_widget_order(
            request.session,
            plane=plane,
            order=order,
        )
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    selected = updated.execution if plane == "execution" else updated.simulation
    return JsonResponse({"status": "ok", "plane": plane, "order": selected})


@login_required
def engine_detail(request: HttpRequest, engine_id: str) -> HttpResponse:
    snapshot = execution_snapshot()
    engine = next(
        (candidate for candidate in snapshot.engines if candidate.engine_id == engine_id),
        None,
    )
    if engine is None:
        raise Http404("Engine not found.")
    return render(
        request,
        "qbet_web/engine_detail.html",
        _context(request, monitoring=snapshot, engine=engine, reports=()),
    )


@login_required
@require_GET
def simulation(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
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
    _require_staff(request)
    if not _simulation_enabled():
        messages.error(request, "Simulation is disabled by the administrator.")
        return redirect("dashboard")

    form = SimulationStartForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "qbet_web/dashboard.html",
            _dashboard_context(request, start_form=form),
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
        return redirect("dashboard")

    if run.report_id is None:
        messages.error(request, "Simulation completed but its report is unavailable.")
        return redirect("dashboard")

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

    values: dict[str, object] = {"form": form}
    if request.user.is_staff:
        control = SIMULATION_CONTROL.snapshot()
        values.update(
            simulation_control=control,
            simulation_toggle_form=SimulationAvailabilityForm(
                initial={"enabled": control.availability.enabled}
            ),
            simulation_enabled=control.availability.enabled,
        )
    return render(request, "qbet_web/settings.html", _context(request, **values))


@login_required
def report_history(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
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
    _require_staff(request)
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
    _require_staff(request)
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
def admin_gui_settings(_: HttpRequest) -> HttpResponse:
    return redirect("presentation-settings")


@user_passes_test(_is_staff, login_url="login")
@require_POST
def admin_simulation_availability(request: HttpRequest) -> HttpResponse:
    form = SimulationAvailabilityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid simulation availability setting.")
        return redirect("presentation-settings")

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
    return redirect("presentation-settings")


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
