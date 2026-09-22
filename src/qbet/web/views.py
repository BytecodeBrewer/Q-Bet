"""Server-rendered control and monitoring views for Q-Bet."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import cast
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import DatabaseError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_GET, require_POST
from pydantic import ValidationError

from qbet.monitoring import MonitoringQuery, MonitoringService as WorkflowMonitoringService
from qbet.monitoring.exports import csv_header as monitoring_csv_header
from qbet.monitoring.exports import csv_rows as monitoring_csv_rows
from qbet.monitoring.exports import json_document as monitoring_json_document
from qbet.notifications import (
    NotificationPreferences,
    PostgresNotificationInbox,
    PostgresNotificationPreferenceRepository,
    notification_recipient_status,
)
from qbet.observability import prometheus_document
from qbet.observability.metrics import ObservabilitySnapshot
from qbet.reporting import (
    CustomerReportUnavailable,
    CustomerReportingQuery,
    CustomerReportingService,
    CustomerResultReport,
    ReportDetailSelection,
    SimulationReport,
)
from qbet.reporting.exports import CustomerReportExport, json_bytes
from qbet.simulation import SimulationEngine
from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
    UserRoutingPreferencePersistenceError,
    UserRoutingPreferenceRepository,
)
from qbet.storage.models import ModeWorkQueueRow
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.observability import (
    ObservabilityPersistenceError,
    PostgresObservabilityRepository,
)
from qbet.storage.postgres import PostgresSimulationReportReader
from qbet.web.account_security import (
    account_verification_status,
    email_verification_token,
    remove_expired_unverified_accounts,
)
from qbet.web.display_preferences import (
    DisplayPreferenceRepository,
    DisplayPreferences,
)
from qbet.web.controls import (
    DashboardLayout,
    PresentationPreferences,
    dashboard_layout,
    presentation_preferences,
    save_dashboard_widget_order,
    save_presentation_preferences,
)
from qbet.web.forms import (
    NotificationPreferencesForm,
    NotificationProfileForm,
    PresentationSettingsForm,
    RegistrationForm,
    SimulationAvailabilityForm,
    SimulationStartForm,
    UserRoutingPreferencesForm,
)
from qbet.web.models import AccountVerification, CustomerReportAccess
from qbet.web.monitoring import MonitoringEngineStatus, MonitoringService, execution_snapshot
from qbet.web.readiness import deployment_release_id, persistence_readiness
from qbet.web.simulation_control import (
    SimulationControlError,
    SimulationControlService,
    SimulationDisableBlockedError,
)
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import (
    RoutingConfiguration,
    UserRoutingPreferences,
    V1Engine,
    engine_modes,
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
    return MonitoringService(PostgresSimulationReportReader())


MONITORING_SERVICE = _monitoring_service()
CUSTOMER_REPORTING_SERVICE = CustomerReportingService(PostgresSimulationReportReader())
NOTIFICATION_PREFERENCES = PostgresNotificationPreferenceRepository()
NOTIFICATION_INBOX = PostgresNotificationInbox()
USER_ROUTING_PREFERENCES = UserRoutingPreferenceRepository()
WORKFLOW_MONITORING_REPOSITORY = PostgresMonitoringRepository()
WORKFLOW_MONITORING_SERVICE = WorkflowMonitoringService(WORKFLOW_MONITORING_REPOSITORY)
SIMULATION_CONTROL = SimulationControlService()
DISPLAY_PREFERENCES = DisplayPreferenceRepository()
OBSERVABILITY_REPOSITORY = PostgresObservabilityRepository()


def _simulation_enabled() -> bool:
    return SIMULATION_CONTROL.availability().enabled


def _routing_configuration() -> tuple[RoutingConfiguration, bool]:
    try:
        return RoutingConfigurationRepository().load() or RoutingConfiguration(), True
    except RoutingConfigurationPersistenceError:
        return RoutingConfiguration(), False


def _execution_runtime_activity() -> tuple[dict[str, tuple[int, int]], bool]:
    """Return actual non-terminal Execution work counts per engine."""

    try:
        rows = ModeWorkQueueRow.objects.filter(
            mode="execution",
            state__in=(
                WorkState.PENDING.value,
                WorkState.PROCESSING.value,
                WorkState.RECHECK.value,
            ),
        ).values("state", "payload")
    except DatabaseError:
        return {}, False

    counts: dict[str, list[int]] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        work = payload.get("work")
        if not isinstance(work, dict):
            continue
        engine_id = str(work.get("engine", ""))
        if not engine_id:
            continue
        values = counts.setdefault(engine_id, [0, 0])
        if row.get("state") == WorkState.PROCESSING.value:
            values[0] += 1
        else:
            values[1] += 1
    return {engine: (values[0], values[1]) for engine, values in counts.items()}, True


def _simulation_runtime_activity(
    control: object,
) -> dict[str, tuple[int, int]]:
    """Return actual persisted Simulation run counts per engine."""

    counts: dict[str, list[int]] = {}
    for run in getattr(control, "runs", ()):
        engine_id = str(getattr(run, "engine", ""))
        if not engine_id:
            continue
        values = counts.setdefault(engine_id, [0, 0])
        status = str(getattr(run, "status", ""))
        if status == "running":
            values[0] += 1
        elif status == "pending":
            values[1] += 1
    return {engine: (values[0], values[1]) for engine, values in counts.items()}


def _user_routing_preferences(user_id: str) -> tuple[UserRoutingPreferences, bool]:
    try:
        return USER_ROUTING_PREFERENCES.load(user_id), True
    except UserRoutingPreferencePersistenceError:
        return UserRoutingPreferences(), False


def _is_staff(user: object) -> bool:
    return bool(getattr(user, "is_staff", False))


def _require_staff(request: HttpRequest) -> None:
    if not _is_staff(request.user):
        raise Http404("This admin-only resource is not available.")


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    if request.user.is_authenticated:
        values.setdefault(
            "display_preferences",
            DISPLAY_PREFERENCES.load(cast(User, request.user)),
        )
    else:
        values.setdefault("display_preferences", DisplayPreferences())
    if "simulation_enabled" not in values:
        values["simulation_enabled"] = bool(
            request.user.is_authenticated and _is_staff(request.user) and _simulation_enabled()
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
    routing_configuration, routing_available = _routing_configuration()
    execution_activity, execution_activity_available = _execution_runtime_activity()
    execution = execution_snapshot(
        routing_configuration,
        configuration_available=routing_available and execution_activity_available,
        runtime_activity=execution_activity,
    )
    values: dict[str, object] = {
        "execution_monitoring": execution,
        "execution_engines": _ordered_engines(execution.engines, layout.execution),
        "execution_layer_active": execution.summary.active_engines > 0,
        "execution_layer_running": execution.summary.running_engines > 0,
        "dashboard_layout": layout,
        "routing_available": routing_available,
        "simulation_enabled": False,
    }
    if _is_staff(request.user) and _simulation_enabled():
        simulation_control = SIMULATION_CONTROL.snapshot()
        simulation_monitoring = MONITORING_SERVICE.snapshot(
            runtime_configuration=routing_configuration,
            runtime_available=routing_available,
            runtime_activity=_simulation_runtime_activity(simulation_control),
        )
        values.update(
            simulation_enabled=True,
            simulation_monitoring=simulation_monitoring,
            simulation_engines=_ordered_engines(
                simulation_monitoring.engines,
                layout.simulation,
            ),
            simulation_layer_active=simulation_monitoring.summary.active_engines > 0,
            simulation_layer_running=simulation_monitoring.summary.running_engines > 0,
            simulation_control=simulation_control,
            start_form=start_form or SimulationStartForm(),
        )
    return _context(request, **values)


def health(_: HttpRequest) -> JsonResponse:
    readiness = persistence_readiness()
    return JsonResponse(
        {
            "status": "ok" if readiness.ready else "unavailable",
            "service": "q-bet-web",
            "persistence": readiness.persistence,
            "readiness": readiness.code.value,
            "runtime": readiness.runtime,
            "release": deployment_release_id(),
        },
        status=200 if readiness.ready else 503,
    )


def home(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/home.html",
        _context(request, monitoring=execution_snapshot()),
    )


def _send_verification_email(request: HttpRequest, user: User) -> bool:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    url = request.build_absolute_uri(reverse("verify-email", args=(uid, token)))
    try:
        delivered = send_mail(
            "Verify your Q-Bet email address",
            f"Verify your email address within 24 hours: {url}",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        return False
    return delivered == 1


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    remove_expired_unverified_accounts()
    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            AccountVerification.objects.create(user=user)
            if not _send_verification_email(request, user):
                user.delete()
                messages.error(
                    request,
                    "Account verification email could not be sent. Please try again.",
                )
                return render(
                    request,
                    "qbet_web/register.html",
                    _context(request, form=RegistrationForm(), registration_error=True),
                    status=503,
                )
            request.session["pending_verification_user_id"] = user.pk
            messages.success(
                request,
                "Check your email to verify this account before signing in.",
            )
            return redirect("verification-pending")
        return render(
            request,
            "qbet_web/register.html",
            _context(request, form=RegistrationForm(), registration_error=True),
        )
    return render(request, "qbet_web/register.html", _context(request, form=form))


def verification_pending(request: HttpRequest) -> HttpResponse:
    pending_user = None
    pending_status = None
    pending_user_id = request.session.get("pending_verification_user_id")
    if pending_user_id is not None:
        try:
            pending_user = User.objects.get(pk=pending_user_id, is_active=False)
        except (User.DoesNotExist, ValueError, TypeError):
            request.session.pop("pending_verification_user_id", None)
        else:
            pending_status = account_verification_status(pending_user)
    return render(
        request,
        "qbet_web/verification_pending.html",
        _context(
            request,
            pending_email=getattr(pending_user, "email", ""),
            verification_status=pending_status,
        ),
    )


def verify_email(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    remove_expired_unverified_accounts()
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
    except (ValueError, TypeError, OverflowError):
        user_id = ""
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user_id, is_active=False)
            verification = AccountVerification.objects.select_for_update().get(user=user)
            state = account_verification_status(user)
            if (
                verification.verified_at is not None
                or state.expired
                or not email_verification_token.check_token(user, token)
            ):
                raise ValueError("invalid verification")
            now = timezone.now()
            user.is_active = True
            user.save(update_fields=("is_active",))
            verification.verified_at = now
            verification.save(update_fields=("verified_at",))
    except (User.DoesNotExist, AccountVerification.DoesNotExist, ValueError):
        messages.error(request, "This verification link is invalid or has expired.")
        return redirect("verification-pending")
    request.session.pop("pending_verification_user_id", None)
    messages.success(request, "Email verified. You can now sign in.")
    return redirect("login")


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    form = NotificationProfileForm(request.POST or None, instance=user)
    preferences = NOTIFICATION_PREFERENCES.load(user.get_username())
    preferences_form = NotificationPreferencesForm(
        request.POST or None,
        initial={
            "email_enabled": preferences.email_enabled,
            "inbox_enabled": preferences.inbox_enabled,
            "categories": preferences.categories,
        },
    )
    if request.method == "POST" and form.is_valid() and preferences_form.is_valid():
        form.save()
        NOTIFICATION_PREFERENCES.save(
            user.get_username(),
            NotificationPreferences(
                email_enabled=bool(preferences_form.cleaned_data["email_enabled"]),
                inbox_enabled=bool(preferences_form.cleaned_data["inbox_enabled"]),
                categories=tuple(preferences_form.cleaned_data["categories"]),
            ),
        )
        messages.success(request, "Profile updated.")
        return redirect("profile")
    status = notification_recipient_status(
        user_id=user.get_username(),
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
    )
    return render(
        request,
        "qbet_web/profile.html",
        _context(
            request,
            form=form,
            preferences_form=preferences_form,
            status=status,
            verification_status=account_verification_status(user),
        ),
    )


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
    if plane == "simulation" and (not _is_staff(request.user) or not _simulation_enabled()):
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
    routing_configuration, routing_available = _routing_configuration()
    snapshot = execution_snapshot(
        routing_configuration,
        configuration_available=routing_available,
    )
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
    routing_configuration, routing_available = _routing_configuration()
    return render(
        request,
        "qbet_web/simulation.html",
        _context(
            request,
            monitoring=MONITORING_SERVICE.snapshot(
                runtime_configuration=routing_configuration,
                runtime_available=routing_available,
            ),
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

    engine = SimulationEngine(form.cleaned_data["engine"])
    routing_configuration, routing_available = _routing_configuration()
    if not routing_available:
        messages.error(request, "Engine control is temporarily unavailable.")
        return redirect("dashboard")
    if not engine_modes(
        routing_configuration,
        cast(V1Engine, engine.value),
    ).simulation:
        messages.error(request, "Start this engine in Simulation before running a pipeline test.")
        return redirect("dashboard")

    try:
        run = SIMULATION_CONTROL.start(
            engine=engine,
            starting_capital=form.cleaned_data["starting_capital"],
            max_duration=timedelta(minutes=form.cleaned_data["max_duration_minutes"]),
        )
    except SimulationControlError as error:
        messages.error(request, str(error))
        return redirect("dashboard")

    if run.report_id is None:
        messages.error(request, "Simulation completed but its report is unavailable.")
        return redirect("dashboard")
    try:
        CustomerReportAccess.objects.get_or_create(
            report_id=run.report_id,
            user=request.user,
        )
    except DatabaseError:
        messages.error(
            request, "Simulation completed but its customer report access is unavailable."
        )
        return redirect("dashboard")

    messages.success(
        request,
        f"Pipeline test {run.run_id} finished with status {run.status}.",
    )
    return redirect("report-detail", run_id=run.report_id)


@login_required
def presentation_settings(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    preferences = presentation_preferences(request.session)
    display_preferences = DISPLAY_PREFERENCES.load(user)
    form = PresentationSettingsForm(
        request.POST or None,
        initial={
            "theme": preferences.theme,
            "font_size": preferences.font_size,
            "language": display_preferences.language,
            "region": display_preferences.region,
            "timezone_name": display_preferences.timezone_name,
            "time_format": display_preferences.time_format,
            "currency": display_preferences.currency,
        },
    )
    if request.method == "POST" and form.is_valid():
        save_presentation_preferences(
            request.session,
            PresentationPreferences(
                theme=form.cleaned_data["theme"],
                font_size=form.cleaned_data["font_size"],
            ),
        )
        try:
            DISPLAY_PREFERENCES.save(
                user,
                DisplayPreferences(
                    language=form.cleaned_data["language"] or display_preferences.language,
                    region=form.cleaned_data["region"] or display_preferences.region,
                    timezone_name=form.cleaned_data["timezone_name"]
                    or display_preferences.timezone_name,
                    time_format=form.cleaned_data["time_format"] or display_preferences.time_format,
                    currency=form.cleaned_data["currency"] or display_preferences.currency,
                ),
            )
        except RuntimeError:
            messages.error(request, "Display preferences are temporarily unavailable.")
        else:
            messages.success(request, "Presentation preferences updated.")
        return redirect("presentation-settings")

    routing_configuration, routing_available = _routing_configuration()
    user_routing, user_routing_available = _user_routing_preferences(
        request.user.get_username()
    )
    engine_preferences_available = routing_available and user_routing_available
    values: dict[str, object] = {
        "form": form,
        "verification_status": account_verification_status(user),
        "engine_preferences_available": engine_preferences_available,
        "engine_preferences_form": (
            UserRoutingPreferencesForm(
                global_configuration=routing_configuration,
                preferences=user_routing,
            )
            if engine_preferences_available
            else None
        ),
    }
    if _is_staff(request.user):
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
@require_POST
def user_routing_preferences_update(request: HttpRequest) -> HttpResponse:
    routing_configuration, routing_available = _routing_configuration()
    if not routing_available:
        messages.error(request, "Engine availability is temporarily unavailable.")
        return redirect("presentation-settings")

    user_id = request.user.get_username()
    try:
        current = USER_ROUTING_PREFERENCES.load(user_id)
    except UserRoutingPreferencePersistenceError:
        messages.error(request, "Your engine preferences are temporarily unavailable.")
        return redirect("presentation-settings")

    form = UserRoutingPreferencesForm(
        request.POST,
        global_configuration=routing_configuration,
        preferences=current,
    )
    if not form.is_valid():
        messages.error(request, "Engine preferences were not accepted.")
        return redirect("presentation-settings")

    try:
        USER_ROUTING_PREFERENCES.save(user_id, form.to_preferences())
    except UserRoutingPreferencePersistenceError:
        messages.error(request, "Your engine preferences could not be saved.")
    else:
        messages.success(request, "Engine and mode preferences updated.")
    return redirect("presentation-settings")


@login_required
def report_history(request: HttpRequest) -> HttpResponse:
    now = datetime.now(UTC)
    visible_report_ids = _visible_customer_report_ids(request)
    preset = request.GET.get("range", "7d")
    start = now - timedelta(hours={"24h": 24, "7d": 168, "30d": 720}.get(preset, 168))
    if preset == "custom":
        try:
            start = datetime.fromisoformat(request.GET["start"]).astimezone(UTC)
            now = datetime.fromisoformat(request.GET["end"]).astimezone(UTC)
        except (KeyError, ValueError):
            preset = "7d"
            start = now - timedelta(days=7)
    try:
        dashboard = CUSTOMER_REPORTING_SERVICE.dashboard(
            CustomerReportingQuery(
                start=start,
                end=now,
                engine=request.GET.get("engine") or None,
                mode=request.GET.get("mode") or None,
                report_ids=(
                    frozenset(visible_report_ids) if visible_report_ids is not None else None
                ),
            )
        )
    except ValueError:
        dashboard = CUSTOMER_REPORTING_SERVICE.dashboard(
            CustomerReportingQuery(
                start=now - timedelta(days=7),
                end=now,
                report_ids=(
                    frozenset(visible_report_ids) if visible_report_ids is not None else None
                ),
            )
        )
        preset = "7d"
    return render(
        request,
        "qbet_web/report_history.html",
        _context(
            request,
            dashboard=dashboard,
            reports=dashboard.reports,
            selected_engine=request.GET.get("engine", ""),
            selected_mode=request.GET.get("mode", ""),
            selected_range=preset,
            report_start=start,
            report_end=now,
        ),
    )


@login_required
def notification_inbox(request: HttpRequest) -> HttpResponse:
    inbox = NOTIFICATION_INBOX.list(request.user.get_username())
    return render(request, "qbet_web/inbox.html", _context(request, inbox=inbox))


@login_required
@require_POST
def notification_inbox_read(request: HttpRequest, task_id: UUID) -> HttpResponse:
    if NOTIFICATION_INBOX.mark_read(request.user.get_username(), task_id):
        messages.success(request, "Notification marked as read.")
    return redirect("notification-inbox")


@login_required
def report_detail(request: HttpRequest, run_id: UUID) -> HttpResponse:
    if not _can_view_customer_report(request, run_id):
        raise Http404("Report not found.")
    lookup = MONITORING_SERVICE.load_report(run_id, ReportDetailSelection())
    if lookup.report is None:
        return render(
            request,
            "qbet_web/report_unavailable.html",
            _context(request, message=lookup.message or "This report is not available."),
            status=404,
        )
    customer_report = _customer_report(lookup.report)
    if customer_report is None:
        return render(
            request,
            "qbet_web/report_unavailable.html",
            _context(
                request,
                message="Required business data is unavailable, so no customer report was created.",
            ),
            status=404,
        )
    return render(
        request,
        "qbet_web/report_detail.html",
        _context(
            request,
            report=customer_report,
        ),
    )


@login_required
def report_export(request: HttpRequest, run_id: UUID, export_format: str) -> HttpResponse:
    if not _can_view_customer_report(request, run_id):
        raise Http404("Report not found.")
    lookup = MONITORING_SERVICE.load_report(run_id, ReportDetailSelection())
    if lookup.report is None:
        raise Http404(lookup.message or "Report not found.")
    customer_report = _customer_report(lookup.report)
    if customer_report is None:
        raise Http404("Customer report is unavailable because required business data is missing.")
    export = CustomerReportExport(customer_report)
    if export_format == "json":
        response = HttpResponse(json_bytes(export), content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.json"'
        return response
    if export_format == "pdf":
        response = HttpResponse(export.pdf_document(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="qbet-report-{run_id}.pdf"'
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


def _customer_report(report: object) -> CustomerResultReport | None:
    if not isinstance(report, SimulationReport):
        return None
    try:
        return CustomerResultReport.from_simulation_report(report)
    except CustomerReportUnavailable:
        return None


def _can_view_customer_report(request: HttpRequest, report_id: UUID) -> bool:
    if _is_staff(request.user):
        return True
    try:
        return CustomerReportAccess.objects.filter(
            report_id=report_id,
            user=request.user,
        ).exists()
    except DatabaseError:
        return False


def _visible_customer_report_ids(request: HttpRequest) -> set[UUID] | None:
    if _is_staff(request.user):
        return None
    try:
        return set(
            CustomerReportAccess.objects.filter(user=request.user).values_list(
                "report_id", flat=True
            )
        )
    except DatabaseError:
        return set()


@user_passes_test(_is_staff, login_url="login")
@require_GET
def monitoring(request: HttpRequest) -> HttpResponse:
    query = _monitoring_query(request)
    mode = request.GET.get("view", "compact")
    if mode not in {"compact", "extended"}:
        raise Http404("Monitoring view not found.")
    records = (
        WORKFLOW_MONITORING_SERVICE.extended(query)
        if mode == "extended"
        else WORKFLOW_MONITORING_SERVICE.compact(query)
    )
    try:
        observability = OBSERVABILITY_REPOSITORY.snapshot(start=query.start, end=query.end)
    except ObservabilityPersistenceError:
        observability = ObservabilitySnapshot.unavailable()
    routing_configuration, routing_available = _routing_configuration()
    execution_activity, execution_activity_available = _execution_runtime_activity()
    simulation_control = SIMULATION_CONTROL.snapshot()
    simulation_runtime = MONITORING_SERVICE.snapshot(
        runtime_configuration=routing_configuration,
        runtime_available=routing_available,
        runtime_activity=_simulation_runtime_activity(simulation_control),
    )
    return render(
        request,
        "qbet_web/monitoring.html",
        _context(
            request,
            monitoring=simulation_runtime,
            execution_runtime=execution_snapshot(
                routing_configuration,
                configuration_available=routing_available and execution_activity_available,
                runtime_activity=execution_activity,
            ),
            simulation_runtime=simulation_runtime,
            routing_available=routing_available,
            monitoring_view=mode,
            monitoring_records=records,
            monitoring_start=query.start,
            monitoring_end=query.end,
            monitoring_query_parameters=_monitoring_query_parameters(request),
            monitoring_range=request.GET.get("range")
            or ("custom" if request.GET.get("start") or request.GET.get("end") else "24h"),
            monitoring_summary=_monitoring_summary(records),
            queue_total=sum(observability.queue_items.values()),
            execution_total=sum(observability.execution_records.values()),
            observability_available=observability.available,
            grafana_url=settings.QBET_GRAFANA_URL,
            vercel_dashboard_url=settings.QBET_VERCEL_DASHBOARD_URL,
            supabase_dashboard_url=settings.QBET_SUPABASE_DASHBOARD_URL,
        ),
    )


@user_passes_test(_is_staff, login_url="login")
@require_GET
def monitoring_export(request: HttpRequest, export_format: str) -> HttpResponse:
    query = _monitoring_query(request)
    mode = request.GET.get("view", "compact")
    if mode not in {"compact", "extended"} or export_format not in {"csv", "json"}:
        raise Http404("Monitoring export not found.")
    values = (
        WORKFLOW_MONITORING_SERVICE.extended(query)
        if mode == "extended"
        else WORKFLOW_MONITORING_SERVICE.compact(query)
    )
    if not values.available:
        message = values.message or "Monitoring history is temporarily unavailable."
        if export_format == "json":
            return JsonResponse(
                {
                    "error": "monitoring_history_unavailable",
                    "message": message,
                },
                status=503,
            )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(("error", "message"))
        writer.writerow(("monitoring_history_unavailable", message))
        return HttpResponse(
            output.getvalue(),
            content_type="text/csv; charset=utf-8",
            status=503,
        )
    if export_format == "json":
        return HttpResponse(
            monitoring_json_document(values), content_type="application/json; charset=utf-8"
        )
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(monitoring_csv_header(extended=mode == "extended"))
    writer.writerows(monitoring_csv_rows(values))
    return HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")


def _monitoring_query(request: HttpRequest) -> MonitoringQuery:
    preset = request.GET.get("range")
    durations = {"1h": timedelta(hours=1), "24h": timedelta(days=1), "7d": timedelta(days=7)}
    if preset in durations:
        end = datetime.now(UTC)
        start = end - durations[preset]
    else:
        end = _query_datetime(request.GET.get("end")) or datetime.now(UTC)
        start = _query_datetime(request.GET.get("start")) or end - timedelta(days=1)
    try:
        correlation = request.GET.get("correlation")
        return MonitoringQuery(
            start=start,
            end=end,
            correlation_id=UUID(correlation) if correlation else None,
        )
    except ValidationError as error:
        raise Http404(error.errors()[0]["msg"]) from error
    except ValueError as error:
        raise Http404("Monitoring correlation identifiers must be UUID values.") from error


def _monitoring_summary(records: object) -> dict[str, int]:
    values = tuple(cast(tuple[object, ...], records))
    return {
        "total": len(values),
        "warnings": sum(
            getattr(record, "level", None) == "warning" or getattr(record, "warning_count", 0) > 0
            for record in values
        ),
        "errors": sum(
            getattr(record, "level", None) == "error" or getattr(record, "error_count", 0) > 0
            for record in values
        ),
    }


@require_GET
def metrics(request: HttpRequest) -> HttpResponse:
    """Expose only aggregate, token-protected durable observability values."""

    token = settings.QBET_METRICS_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not token or not constant_time_compare(supplied, f"Bearer {token}"):
        return HttpResponse("Not found.", status=404, content_type="text/plain; charset=utf-8")
    end = datetime.now(UTC)
    try:
        snapshot = OBSERVABILITY_REPOSITORY.cumulative_snapshot(end=end)
    except ObservabilityPersistenceError:
        snapshot = ObservabilitySnapshot.unavailable()
    return HttpResponse(
        prometheus_document(snapshot),
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )


def _query_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Http404("Monitoring timestamps must be ISO-8601 values.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Http404("Monitoring timestamps must include a timezone.")
    return parsed


def _monitoring_query_parameters(request: HttpRequest) -> str:
    parameters = request.GET.copy()
    parameters.pop("view", None)
    return parameters.urlencode()


@user_passes_test(_is_staff, login_url="login")
def admin_area(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "qbet_web/admin_area.html",
        _context(
            request,
            metrics_enabled=bool(settings.QBET_METRICS_TOKEN),
            grafana_url=settings.QBET_GRAFANA_URL,
            vercel_dashboard_url=settings.QBET_VERCEL_DASHBOARD_URL,
            supabase_dashboard_url=settings.QBET_SUPABASE_DASHBOARD_URL,
        ),
    )


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
