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

from qbet.execution.models import Lifecycle
from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.web.controls import presentation_preferences
from qbet.web.display_preferences import DisplayPreferenceRepository, DisplayPreferences
from qbet.web.ui_copy import ui_copy
from qbet.workflow.approval import ExecutionApprovalService

_EXECUTION_APPROVALS = ExecutionApprovalService()
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
        approvals = _EXECUTION_APPROVALS.pending_for(request.user.get_username())
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return render(
            request,
            "qbet_web/execution_approvals.html",
            _context(
                request,
                approvals=(),
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
