"""Server-rendered control and monitoring views for Q-Bet."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import cast
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import DatabaseError, transaction
from django.forms import ChoiceField
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from pydantic import ValidationError

from qbet.domain.models import Currency
from qbet.web.avatars import (
    AvatarStorageError,
    AvatarValidationError,
    avatar_fallback_svg,
    get_avatar_storage,
    is_valid_stored_avatar,
    normalize_avatar,
)
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
from qbet.web.engine_notices import (
    bonus_input_snapshot,
    contextual_engine_statuses,
    provider_activity_notice,
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
    PipelineDryRunForm,
    PresentationSettingsForm,
    RegistrationForm,
    SimulationAvailabilityForm,
    SimulationPortfolioResetForm,
    SimulationPortfolioSeedForm,
    SimulationStartForm,
    UserRoutingPreferencesForm,
)
from qbet.web.models import (
    AccountVerification,
    CustomerReportAccess,
    SimulationRunState,
    UserAvatar,
)
from qbet.web.monitoring import MonitoringEngineStatus, MonitoringService, execution_snapshot
from qbet.web.provider_activity import ProviderActivitySnapshot, provider_activity_snapshot
from qbet.web.portfolio import PortfolioCapitalReadService
from qbet.web.portfolio_locations import ProviderCapitalLocationForm
from qbet.web.readiness import deployment_release_id, persistence_readiness
from qbet.web.shell_context import display_preferences_for, shell_context
from qbet.web.ui_copy import ui_copy
from qbet.web.simulation_control import (
    SimulationControlError,
    SimulationControlService,
    SimulationDisableBlockedError,
)
from qbet.workflow.queue import WorkState
from qbet.workflow.readiness import Phase2PipelineReadiness
from qbet.workflow.routing import (
    RoutingConfiguration,
    UserRoutingPreferences,
    V1Engine,
    connected_product_routing_configuration,
    engine_modes,
)
from qbet.workflow import WorkflowMode

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
PORTFOLIO_CAPITAL = PortfolioCapitalReadService()


def _simulation_enabled() -> bool:
    return SIMULATION_CONTROL.availability().enabled


def _routing_configuration() -> tuple[RoutingConfiguration, bool]:
    try:
        stored = RoutingConfigurationRepository().load()
        return connected_product_routing_configuration(stored) or RoutingConfiguration(), True
    except RoutingConfigurationPersistenceError:
        return RoutingConfiguration(), False


def _execution_runtime_activity() -> tuple[dict[str, tuple[int, int]], bool]:
    """Return actual non-terminal Execution work counts per engine."""

    try:
        rows = tuple(
            ModeWorkQueueRow.objects.filter(
                mode="execution",
                state__in=(
                    WorkState.PENDING.value,
                    WorkState.PROCESSING.value,
                    WorkState.RECHECK.value,
                ),
            ).values("state", "payload")
        )
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


def _provider_activity(correlation_id: UUID | None = None) -> ProviderActivitySnapshot:
    now = datetime.now(UTC)
    records = WORKFLOW_MONITORING_SERVICE.extended(
        MonitoringQuery(
            start=now - timedelta(minutes=10),
            end=now,
            correlation_id=correlation_id,
        )
    )
    return provider_activity_snapshot(
        records,
        now=now,
        available=records.available,
    )


@login_required
@require_GET
def provider_activity(request: HttpRequest) -> JsonResponse:
    correlation = request.GET.get("correlation")
    if correlation and not _is_staff(request.user):
        raise Http404("Correlation-scoped provider activity is staff-only.")
    try:
        correlation_id = UUID(correlation) if correlation else None
    except ValueError:
        return JsonResponse(
            {"state": "error", "label": "Provider activity filter is invalid."},
            status=400,
        )

    activity = _provider_activity(correlation_id)
    payload: dict[str, object] = {
        "state": activity.state,
        "label": activity.label,
        "occurred_at": activity.occurred_at.isoformat() if activity.occurred_at else None,
    }
    if _is_staff(request.user):
        payload.update(
            provider=activity.provider,
            duration_ms=activity.duration_ms,
            reason_code=activity.reason_code,
        )
    return JsonResponse(payload)


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
    for key, value in shell_context(request).items():
        values.setdefault(key, value)
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
    user = cast(User, request.user)
    bonus_input = bonus_input_snapshot(user_id=cast(int, user.pk))
    provider_activity = _provider_activity()
    execution_engines = contextual_engine_statuses(
        execution.engines,
        bonus_input=bonus_input,
        bonus_offers_url=reverse("bonus-offer-list"),
    )
    values: dict[str, object] = {
        "execution_monitoring": execution,
        "execution_engines": _ordered_engines(execution_engines, layout.execution),
        "execution_attention": any(
            engine.enabled and notice.severity in {"warning", "error"}
            for engine in execution_engines
            for notice in engine.notices
        ),
        "execution_layer_active": execution.summary.active_engines > 0,
        "execution_layer_running": execution.summary.running_engines > 0,
        "dashboard_layout": layout,
        "routing_available": routing_available,
        "provider_activity": provider_activity,
        "provider_notice": provider_activity_notice(provider_activity),
        "simulation_enabled": False,
    }
    if _is_staff(request.user) and _simulation_enabled():
        simulation_control = SIMULATION_CONTROL.snapshot()
        start_form_value = start_form or SimulationStartForm()
        available_currencies = tuple(
            (balance.currency, balance.currency)
            for balance in simulation_control.portfolio.balances
        )
        cast(ChoiceField, start_form_value.fields["currency"]).choices = (
            available_currencies
            or (("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD"))
        )
        simulation_monitoring = MONITORING_SERVICE.snapshot(
            runtime_configuration=routing_configuration,
            runtime_available=routing_available,
            runtime_activity=_simulation_runtime_activity(simulation_control),
        )
        simulation_engines = contextual_engine_statuses(
            simulation_monitoring.engines,
            bonus_input=bonus_input,
            runs=simulation_control.runs,
            bonus_offers_url=reverse("bonus-offer-list"),
        )
        values.update(
            simulation_enabled=True,
            simulation_monitoring=simulation_monitoring,
            simulation_engines=_ordered_engines(
                simulation_engines,
                layout.simulation,
            ),
            simulation_layer_active=simulation_monitoring.summary.active_engines > 0,
            simulation_layer_running=simulation_monitoring.summary.running_engines > 0,
            simulation_control=simulation_control,
            start_form=start_form_value,
            simulation_portfolio_seed_form=SimulationPortfolioSeedForm(),
            simulation_portfolio_reset_form=SimulationPortfolioResetForm(),
            simulation_return_to="dashboard",
        )
    return _context(request, **values)


@require_GET
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


@require_GET
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


@require_http_methods(["GET", "POST"])
def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
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


@require_GET
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


@require_http_methods(["GET", "POST"])
def verify_email(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
    except (ValueError, TypeError, OverflowError):
        user_id = ""

    if request.method == "GET":
        try:
            user = User.objects.get(pk=user_id, is_active=False)
            verification = AccountVerification.objects.get(user=user)
            state = account_verification_status(user)
            if (
                verification.verified_at is not None
                or state.expired
                or not email_verification_token.check_token(user, token)
            ):
                raise ValueError("invalid verification")
        except (User.DoesNotExist, AccountVerification.DoesNotExist, ValueError):
            messages.error(request, "This verification link is invalid or has expired.")
            return redirect("verification-pending")
        return render(request, "qbet_web/verification_confirm.html", _context(request))

    remove_expired_unverified_accounts()
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
@require_http_methods(["GET", "POST"])
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
            avatar=UserAvatar.objects.filter(user=user).first(),
        ),
    )


@login_required
@require_POST
def profile_avatar_update(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    uploaded_file = request.FILES.get("avatar")
    if uploaded_file is None:
        messages.error(request, "Choose an image to upload.")
        return redirect("profile")
    try:
        normalized = normalize_avatar(uploaded_file)
        storage = get_avatar_storage()
        object_key = f"{user.pk}/{uuid4().hex}.jpg"
        storage.upload(object_key, normalized.content)
    except AvatarValidationError as exc:
        messages.error(request, str(exc))
        return redirect("profile")
    except AvatarStorageError:
        messages.error(request, "Avatar storage is unavailable. Please try again later.")
        return redirect("profile")

    try:
        with transaction.atomic():
            avatar = UserAvatar.objects.select_for_update().filter(user=user).first()
            previous_key = avatar.object_key if avatar is not None else None
            if avatar is None:
                UserAvatar.objects.create(
                    user=user,
                    object_key=object_key,
                    content_type="image/jpeg",
                    byte_size=len(normalized.content),
                )
            else:
                avatar.object_key = object_key
                avatar.content_type = "image/jpeg"
                avatar.byte_size = len(normalized.content)
                avatar.save(update_fields=("object_key", "content_type", "byte_size", "updated_at"))
    except DatabaseError:
        try:
            storage.delete(object_key)
        except AvatarStorageError:
            pass
        raise

    if previous_key:
        try:
            storage.delete(previous_key)
        except AvatarStorageError:
            pass
    messages.success(request, "Profile picture updated.")
    return redirect("profile")


@login_required
@require_POST
def profile_avatar_remove(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    with transaction.atomic():
        avatar = UserAvatar.objects.select_for_update().filter(user=user).first()
        object_key = avatar.object_key if avatar is not None else None
        if avatar is not None:
            avatar.delete()
    if object_key is None:
        messages.success(request, "The generated profile picture is active.")
        return redirect("profile")
    try:
        get_avatar_storage().delete(object_key)
    except AvatarStorageError:
        messages.warning(
            request,
            "Your generated profile picture is active, but stored image cleanup could not finish.",
        )
        return redirect("profile")
    messages.success(request, "Profile picture removed.")
    return redirect("profile")


@login_required
@require_GET
def account_avatar(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    try:
        avatar = UserAvatar.objects.get(user=user)
        content = get_avatar_storage().download(avatar.object_key)
        if avatar.content_type != "image/jpeg" or not is_valid_stored_avatar(content):
            raise AvatarStorageError("Stored avatar is invalid.")
    except (UserAvatar.DoesNotExist, AvatarStorageError):
        content = avatar_fallback_svg(user.get_username(), user.first_name, user.last_name)
        response = HttpResponse(content, content_type="image/svg+xml")
    else:
        response = HttpResponse(content, content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
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
@require_GET
def engine_detail(request: HttpRequest, engine_id: str) -> HttpResponse:
    routing_configuration, routing_available = _routing_configuration()
    execution_activity, execution_activity_available = _execution_runtime_activity()
    snapshot = execution_snapshot(
        routing_configuration,
        configuration_available=routing_available and execution_activity_available,
        runtime_activity=execution_activity,
    )
    user = cast(User, request.user)
    enriched = contextual_engine_statuses(
        snapshot.engines,
        bonus_input=bonus_input_snapshot(user_id=cast(int, user.pk)),
        bonus_offers_url=reverse("bonus-offer-list"),
    )
    engine = next(
        (candidate for candidate in enriched if candidate.engine_id == engine_id),
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
    auto_run = request.GET.get("autostart", "")
    monitoring = MONITORING_SERVICE.snapshot(
        runtime_configuration=routing_configuration,
        runtime_available=routing_available,
        runtime_activity=_simulation_runtime_activity(control),
    )
    user = cast(User, request.user)
    monitoring = replace(
        monitoring,
        engines=contextual_engine_statuses(
            monitoring.engines,
            bonus_input=bonus_input_snapshot(user_id=cast(int, user.pk)),
            runs=control.runs,
            bonus_offers_url=reverse("bonus-offer-list"),
        ),
    )
    provider_activity = _provider_activity()
    return render(
        request,
        "qbet_web/simulation.html",
        _context(
            request,
            monitoring=monitoring,
            simulation_control=control,
            start_form=_simulation_start_form(control),
            simulation_portfolio_seed_form=SimulationPortfolioSeedForm(),
            simulation_portfolio_reset_form=SimulationPortfolioResetForm(),
            simulation_return_to="simulation",
            pipeline_dry_run_form=PipelineDryRunForm(),
            pipeline_dry_run=request.session.pop("pipeline_dry_run", None),
            provider_activity=provider_activity,
            provider_notice=provider_activity_notice(provider_activity),
            auto_run_id=auto_run,
            simulation_enabled=True,
        ),
    )


def _simulation_start_form(control) -> SimulationStartForm:
    form = SimulationStartForm()
    currencies = tuple(
        (balance.currency, balance.currency)
        for balance in control.portfolio.balances
    )
    if currencies:
        cast(ChoiceField, form.fields["currency"]).choices = currencies
    return form


def _simulation_return_target(request: HttpRequest) -> str:
    target = request.POST.get("return_to")
    return target if target in {"dashboard", "simulation"} else "dashboard"


@login_required
@require_POST
def simulation_portfolio_seed(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
    target = _simulation_return_target(request)
    form = SimulationPortfolioSeedForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid virtual seed amount and currency.")
        return redirect(target)
    try:
        SIMULATION_CONTROL.seed_portfolio(
            amount=form.cleaned_data["amount"],
            currency=cast(Currency, form.cleaned_data["currency"]),
        )
    except SimulationControlError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Simulation sandbox portfolio initialized.")
    return redirect(target)


@login_required
@require_POST
def simulation_portfolio_reset(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
    target = _simulation_return_target(request)
    form = SimulationPortfolioResetForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirm the Simulation sandbox reset to continue.")
        return redirect(target)
    try:
        SIMULATION_CONTROL.reset_portfolio(reset_by=cast(User, request.user))
    except SimulationControlError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            "Simulation sandbox returned to its seed balance. Prior ledger state was archived.",
        )
    return redirect(target)


@login_required
@require_POST
def simulation_start(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
    if not _simulation_enabled():
        messages.error(request, "Simulation is disabled by the administrator.")
        return redirect("dashboard")

    control = SIMULATION_CONTROL.snapshot()
    form = SimulationStartForm(request.POST, initial={"currency": "EUR"})
    currencies = tuple(
        (balance.currency, balance.currency)
        for balance in control.portfolio.balances
    )
    if currencies:
        cast(ChoiceField, form.fields["currency"]).choices = currencies
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
        messages.error(request, "Enable this engine for Simulation before starting a simulation.")
        return redirect("dashboard")

    try:
        run = SIMULATION_CONTROL.begin(
            engine=engine,
            currency=cast(Currency, form.cleaned_data["currency"]),
            initiated_by=cast(User, request.user),
        )
    except SimulationControlError as error:
        messages.error(request, str(error))
        return redirect("dashboard")

    messages.success(request, f"Simulation {run.run_id} started.")
    return redirect(f"{reverse('simulation')}?autostart={run.run_id}")


@login_required
@require_POST
def simulation_run(request: HttpRequest, run_id: UUID) -> HttpResponse:
    _require_staff(request)
    if not SimulationRunState.objects.filter(
        pk=run_id,
        initiated_by=request.user,
    ).exists():
        raise Http404("Simulation run was not found.")
    try:
        run = SIMULATION_CONTROL.run(run_id)
    except SimulationControlError as error:
        return JsonResponse(
            {"status": "error", "message": str(error)},
            status=409,
        )

    report_url = None
    if run.report_id is not None:
        try:
            CustomerReportAccess.objects.get_or_create(
                report_id=run.report_id,
                user=request.user,
            )
        except DatabaseError:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "Simulation finished but its customer report access is unavailable.",
                },
                status=503,
            )
        report_url = reverse("report-detail", args=(run.report_id,))

    return JsonResponse(
        {
            "status": run.status,
            "run_id": str(run.run_id),
            "progress": str(run.progress),
            "report_url": report_url,
        }
    )


@login_required
@require_POST
def simulation_stop(request: HttpRequest, run_id: UUID) -> HttpResponse:
    _require_staff(request)
    if not SimulationRunState.objects.filter(
        pk=run_id,
        initiated_by=request.user,
    ).exists():
        raise Http404("Simulation run was not found.")
    try:
        run = SIMULATION_CONTROL.stop(run_id)
    except SimulationControlError as error:
        return JsonResponse(
            {"status": "error", "message": str(error)},
            status=409,
        )
    return JsonResponse(
        {
            "status": run.status,
            "run_id": str(run.run_id),
            "progress": str(run.progress),
        }
    )


@login_required
@require_POST
def pipeline_dry_run(request: HttpRequest) -> HttpResponse:
    _require_staff(request)
    form = PipelineDryRunForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid engine and mode for the pipeline dry-run.")
        return redirect("simulation")

    engine = cast(V1Engine, str(form.cleaned_data["engine"]))
    mode = WorkflowMode(str(form.cleaned_data["mode"]))
    stages = Phase2PipelineReadiness().snapshot(engine, mode)
    request.session["pipeline_dry_run"] = {
        "engine": engine,
        "mode": mode.value,
        "stages": [
            {
                "name": stage.stage.value.replace("_", " ").title(),
                "ready": stage.ready,
                "detail": "Ready" if stage.ready else (stage.reason or "Unavailable"),
            }
            for stage in stages
        ],
    }
    messages.success(
        request,
        "Pipeline dry-run completed without provider, opportunity, order, or capital side effects.",
    )
    return redirect("simulation")


@login_required
@require_http_methods(["GET", "POST"])
def presentation_settings(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    preferences = presentation_preferences(request.session)
    display_preferences = display_preferences_for(request)
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


def _report_history_selection(
    request: HttpRequest,
    *,
    strict: bool = False,
) -> tuple[object, str, datetime, datetime]:
    now = datetime.now(UTC)
    visible_report_ids = _visible_customer_report_ids(request)
    preset = request.GET.get("range", "7d")
    if preset not in {"1h", "24h", "7d", "30d", "custom"}:
        if strict:
            raise ValueError("Choose a supported report period.")
        messages.error(request, "Choose a supported report period.")
        preset = "7d"
    start = now - timedelta(hours={"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(preset, 168))
    if preset == "custom":
        try:
            start_value = _query_datetime(request.GET.get("start"))
            end_value = _query_datetime(request.GET.get("end"))
            if start_value is None or end_value is None:
                raise ValueError("missing report range")
            start = start_value
            now = end_value
        except (Http404, ValueError):
            if strict:
                raise ValueError("Enter a valid report start and end date and time.") from None
            messages.error(request, "Enter a valid start and end date and time.")
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
    except ValueError as error:
        if strict:
            raise ValueError(
                "Invalid report filters. Check the period, engine, and mode."
            ) from error
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
        start = now - timedelta(days=7)
        messages.error(request, "Choose a valid report range up to the supported reporting limit.")
    return dashboard, preset, start, now


@login_required
@require_GET
def report_history(request: HttpRequest) -> HttpResponse:
    dashboard, preset, start, end = _report_history_selection(request)
    reports = getattr(dashboard, "reports", ())
    display_preferences = display_preferences_for(request)
    currency_summaries = tuple(
        sorted(
            getattr(dashboard, "currency_summaries", ()),
            key=lambda summary: (
                summary.currency != display_preferences.currency,
                summary.currency,
            ),
        )
    )
    return render(
        request,
        "qbet_web/report_history.html",
        _context(
            request,
            dashboard=dashboard,
            reports=reports,
            report_currency_summaries=currency_summaries,
            selected_engine=request.GET.get("engine", ""),
            selected_mode=request.GET.get("mode", ""),
            selected_range=preset,
            report_start=start,
            report_end=end,
        ),
    )


@login_required
@require_GET
def report_history_export(request: HttpRequest, export_format: str) -> HttpResponse:
    if export_format not in {"csv", "json"}:
        raise Http404("Report export format not found.")

    try:
        dashboard, _, start, end = _report_history_selection(request, strict=True)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    reports = tuple(getattr(dashboard, "reports", ()))
    mode = request.GET.get("mode") or "all"
    filename = (
        f"qbet-reports-{mode}-{start.strftime('%Y%m%dT%H%MZ')}-"
        f"{end.strftime('%Y%m%dT%H%MZ')}.{export_format}"
    )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if not reports:
        headers["X-QBet-Export-State"] = "no-data"

    if export_format == "json":
        payload = [
            {
                "report_id": str(report.report_id),
                "mode": report.mode,
                "match": report.match,
                "provider": report.provider,
                "counterparty_provider": report.counterparty_provider,
                "engine": report.engine,
                "strategy": report.strategy,
                "invested_capital": str(report.invested_capital),
                "result_state": report.result_state,
                "profit_loss": str(report.profit_loss),
                "current_capital": str(report.current_capital),
                "currency": report.currency,
                "completed_at": report.completed_at.isoformat(),
            }
            for report in reports
        ]
        return JsonResponse(payload, safe=False, headers=headers)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        (
            "report_id",
            "mode",
            "match",
            "provider",
            "counterparty_provider",
            "engine",
            "strategy",
            "invested_capital",
            "result_state",
            "profit_loss",
            "current_capital",
            "currency",
            "completed_at",
        )
    )
    writer.writerows(
        (
            str(report.report_id),
            report.mode,
            report.match,
            report.provider,
            report.counterparty_provider,
            report.engine,
            report.strategy,
            str(report.invested_capital),
            report.result_state,
            str(report.profit_loss),
            str(report.current_capital),
            report.currency,
            report.completed_at.isoformat(),
        )
        for report in reports
    )
    return HttpResponse(
        output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers=headers,
    )


@login_required
@require_GET
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
@require_GET
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
@require_GET
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
    try:
        query = _monitoring_query(request)
    except Http404 as error:
        messages.error(request, f"Monitoring filters are invalid: {error}.")
        end = datetime.now(UTC)
        query = MonitoringQuery(start=end - timedelta(days=1), end=end)
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
            provider_activity=_provider_activity(),
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
    try:
        query = _monitoring_query(request)
    except Http404 as error:
        message = f"Monitoring filters are invalid: {error}."
        if export_format == "json":
            return JsonResponse({"error": "invalid_monitoring_filter", "message": message}, status=400)
        return HttpResponse(message, content_type="text/plain; charset=utf-8", status=400)
    mode = request.GET.get("view", "compact")
    if mode not in {"compact", "extended"} or export_format not in {"csv", "json"}:
        raise Http404("Monitoring export not found.")
    values = (
        WORKFLOW_MONITORING_SERVICE.extended(query)
        if mode == "extended"
        else WORKFLOW_MONITORING_SERVICE.compact(query)
    )
    filename = _monitoring_export_filename(query, mode, export_format)
    if not values.available:
        message = values.message or "Monitoring history is temporarily unavailable."
        if export_format == "json":
            return JsonResponse(
                {
                    "error": "monitoring_history_unavailable",
                    "message": message,
                },
                status=503,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(("error", "message"))
        writer.writerow(("monitoring_history_unavailable", message))
        response = HttpResponse(
            output.getvalue(),
            content_type="text/csv; charset=utf-8",
            status=503,
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    if export_format == "json":
        response = HttpResponse(
            monitoring_json_document(values), content_type="application/json; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(monitoring_csv_header(extended=mode == "extended"))
    writer.writerows(monitoring_csv_rows(values))
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _monitoring_export_filename(
    query: MonitoringQuery,
    mode: str,
    export_format: str,
) -> str:
    """Use a concise UTC range so downloaded staff exports remain identifiable."""

    start = query.start.strftime("%Y%m%dT%H%MZ")
    end = query.end.strftime("%Y%m%dT%H%MZ")
    return f"qbet-monitoring-{mode}-{start}-{end}.{export_format}"


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
        return timezone.make_aware(parsed, timezone.get_current_timezone()).astimezone(UTC)
    return parsed.astimezone(UTC)


def _monitoring_query_parameters(request: HttpRequest) -> str:
    parameters = request.GET.copy()
    parameters.pop("view", None)
    return parameters.urlencode()


@user_passes_test(_is_staff, login_url="login")
@require_GET
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
@require_GET
def portfolio(request: HttpRequest) -> HttpResponse:
    """Render authoritative capital state without deriving balances from engines."""

    return render(
        request,
        "qbet_web/portfolio.html",
        _context(
            request,
            portfolio=PORTFOLIO_CAPITAL.snapshot(
                user_id=cast(int, request.user.pk),
                is_staff=_is_staff(request.user),
            ),
            portfolio_location_form=ProviderCapitalLocationForm(),
        ),
    )


@login_required
@require_GET
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
