"""Customer-safe Django boundary for explicit execution decisions."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from qbet.execution.models import Lifecycle
from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.web.controls import presentation_preferences
from qbet.workflow.approval import ExecutionApprovalService

_EXECUTION_APPROVALS = ExecutionApprovalService()


def _context(request: HttpRequest, **values: object) -> dict[str, object]:
    values.setdefault("preferences", presentation_preferences(request.session))
    return values


@login_required
@require_GET
def execution_approvals(request: HttpRequest) -> HttpResponse:
    try:
        approvals = _EXECUTION_APPROVALS.pending_for(request.user.get_username())
    except AuthoritativePersistenceError:
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
        record = _EXECUTION_APPROVALS.decide(
            execution_id,
            actor=request.user.get_username(),
            approve=decision == "approve",
        )
    except (KeyError, PermissionError) as error:
        raise Http404("Execution approval not found.") from error
    except (AuthoritativePersistenceError, ValueError):
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
    else:
        messages.info(request, "This execution decision was already finalized.")
    return redirect("execution-approvals")
