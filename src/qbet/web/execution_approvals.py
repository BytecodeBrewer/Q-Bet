"""Customer-safe Django boundary for explicit execution decisions."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from qbet.execution.models import Lifecycle, ManualExecutionDecision
from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.web.controls import presentation_preferences
from qbet.web.display_preferences import DisplayPreferenceRepository, DisplayPreferences
from qbet.web.ui_copy import ui_copy
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.manual_execution import ManualExecutionService

_EXECUTION_APPROVALS = ExecutionApprovalService()
_MANUAL_EXECUTION = ManualExecutionService()
_DISPLAY_PREFERENCES = DisplayPreferenceRepository()


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    display_preferences = (
        _DISPLAY_PREFERENCES.load(cast(User, request.user))
        if request.user.is_authenticated
        else DisplayPreferences()
    )
    values.setdefault("display_preferences", display_preferences)
    values.setdefault("ui", ui_copy(display_preferences.language))
    return values


def _notification_recipient(request: HttpRequest) -> tuple[str, str]:
    username = request.user.get_username()
    get_full_name = getattr(request.user, "get_full_name", None)
    full_name = str(get_full_name()).strip() if callable(get_full_name) else ""
    return str(getattr(request.user, "email", "") or "").strip(), full_name or username


@login_required
@require_GET
def execution_approvals(request: HttpRequest) -> HttpResponse:
    try:
        owner = request.user.get_username()
        approvals = _EXECUTION_APPROVALS.pending_for(owner)
        manual_actions = _MANUAL_EXECUTION.pending_for(owner)
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return render(
            request,
            "qbet_web/execution_approvals.html",
            _context(
                request,
                approvals=(),
                manual_actions=(),
                approvals_available=False,
            ),
            status=503,
        )

    return render(
        request,
        "qbet_web/execution_approvals.html",
        _context(
            request,
            approvals=approvals,
            manual_actions=manual_actions,
            approvals_available=True,
        ),
    )


@login_required
@require_POST
def execution_approval_decision(
    request: HttpRequest,
    execution_id: UUID,
) -> HttpResponse:
    decision = request.POST.get("decision")
    if decision not in {"approve", "reject"}:
        raise Http404("Execution decision not found.")

    try:
        notification_email, notification_display_name = _notification_recipient(request)
        record = _EXECUTION_APPROVALS.decide(
            execution_id,
            actor=request.user.get_username(),
            approve=decision == "approve",
            notification_email=notification_email,
            notification_display_name=notification_display_name,
        )
    except (KeyError, PermissionError) as error:
        raise Http404("Execution approval not found.") from error
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        messages.error(
            request,
            "This execution decision could not be recorded safely. Please try again later.",
        )
        return redirect("execution-approvals")

    if record.state is Lifecycle.APPROVED:
        messages.success(
            request,
            "Approval recorded. The opportunity will be revalidated before dispatch.",
        )
    elif record.state is Lifecycle.REJECTED:
        messages.success(request, "Execution rejected. No order was dispatched.")
    elif record.state is Lifecycle.CANCELLED and record.error == "approval_expired":
        messages.error(request, "This approval expired. No order was dispatched.")
    else:
        messages.info(request, "This execution decision was already finalized.")
    return redirect("execution-approvals")



@login_required
@require_POST
def execution_manual_confirmation(
    request: HttpRequest,
    execution_id: UUID,
) -> HttpResponse:
    allowed_fields = {"csrfmiddlewaretoken", "decision", "note"}
    if set(request.POST) - allowed_fields:
        raise Http404("Execution confirmation not found.")
    try:
        decision = ManualExecutionDecision(str(request.POST.get("decision") or ""))
    except ValueError as error:
        raise Http404("Execution confirmation not found.") from error

    note = str(request.POST.get("note") or "").strip()
    if len(note) > 500:
        messages.error(request, "The confirmation note is too long.")
        return redirect("execution-approvals")

    try:
        record = _MANUAL_EXECUTION.confirm(
            execution_id,
            actor=request.user.get_username(),
            decision=decision,
            note=note,
        )
    except (KeyError, PermissionError) as error:
        raise Http404("Execution action not found.") from error
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        messages.error(
            request,
            "This manual action could not be confirmed safely. Please review the current state.",
        )
        return redirect("execution-approvals")

    if record.state is Lifecycle.ACKNOWLEDGED:
        messages.success(
            request,
            "Action recorded as completed. The execution is pending result settlement.",
        )
    elif record.state is Lifecycle.CANCELLED:
        messages.success(
            request,
            "Action recorded as not completed. Eligible reserved capital was released.",
        )
    elif record.state is Lifecycle.ACTION_PROBLEM:
        messages.warning(
            request,
            "Problem recorded. Reserved capital remains held until you resolve the action.",
        )
    else:
        messages.info(request, "This manual action was already finalized.")
    return redirect("execution-approvals")
