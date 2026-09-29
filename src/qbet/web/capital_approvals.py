"""Authenticated operator boundary for capital movement approvals and manual completion."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from qbet.bank.funding import FundingProposalState
from qbet.storage.capital_movement import CapitalMovementRepository
from qbet.storage.capital_workflow import (
    CapitalActionMethod,
    CapitalFundingWorkflowConflict,
    CapitalFundingWorkflowError,
    CapitalFundingWorkflowRepository,
)
from qbet.web.controls import presentation_preferences
from qbet.web.display_preferences import DisplayPreferenceRepository, DisplayPreferences
from qbet.web.ui_copy import ui_copy

_CAPITAL_WORKFLOW = CapitalFundingWorkflowRepository()
_CAPITAL_MOVEMENTS = CapitalMovementRepository()
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


def _proposal_view(record) -> dict[str, object]:
    proposal = record.proposal
    movement = _CAPITAL_MOVEMENTS.load_by_proposal(proposal.id)
    manual_instruction = None
    if (
        movement is None
        and record.action_method is CapitalActionMethod.MANUAL
        and proposal.state is FundingProposalState.APPROVED
    ):
        manual_instruction = _CAPITAL_WORKFLOW.manual_instruction(
            proposal.id,
            actor=record.owner_id,
        )
    return {
        "record": record,
        "proposal": proposal,
        "movement": movement,
        "manual_instruction": manual_instruction,
        "awaiting_approval": proposal.state is FundingProposalState.AWAITING_APPROVAL,
        "approved": proposal.state is FundingProposalState.APPROVED,
        "rejected": proposal.state is FundingProposalState.REJECTED,
        "manual": record.action_method is CapitalActionMethod.MANUAL,
        "sandbox": record.action_method is CapitalActionMethod.SANDBOX_ADAPTER,
    }


@login_required
@require_GET
def capital_approvals(request: HttpRequest) -> HttpResponse:
    try:
        records = _CAPITAL_WORKFLOW.list_for(request.user.get_username())
        approvals = tuple(_proposal_view(record) for record in records)
    except (CapitalFundingWorkflowError, DatabaseError, ValueError):
        return render(
            request,
            "qbet_web/capital_approvals.html",
            _context(request, approvals=(), approvals_available=False),
            status=503,
        )
    return render(
        request,
        "qbet_web/capital_approvals.html",
        _context(request, approvals=approvals, approvals_available=True),
    )


@login_required
@require_POST
def capital_approval_decision(
    request: HttpRequest,
    proposal_id: UUID,
) -> HttpResponse:
    decision = request.POST.get("decision")
    if decision not in {"approve", "reject"}:
        raise Http404("Capital decision not found.")

    try:
        record = _CAPITAL_WORKFLOW.decide(
            proposal_id,
            actor=request.user.get_username(),
            approve=decision == "approve",
            decided_at=timezone.now(),
        )
    except (KeyError, PermissionError) as error:
        raise Http404("Capital approval not found.") from error
    except CapitalFundingWorkflowConflict as error:
        messages.error(request, f"Capital decision was not recorded: {error}.")
        return redirect("capital-approvals")
    except (CapitalFundingWorkflowError, DatabaseError, ValueError):
        messages.error(
            request,
            "This capital decision could not be recorded safely. No movement was started.",
        )
        return redirect("capital-approvals")

    if record.proposal.state is FundingProposalState.APPROVED:
        messages.success(
            request,
            "Capital movement approved. Review the exact action before continuing.",
        )
    elif record.proposal.state is FundingProposalState.REJECTED:
        messages.success(request, "Capital movement rejected. No transfer action is available.")
    else:
        messages.info(request, "This capital decision was already finalized.")
    return redirect("capital-approvals")


@login_required
@require_POST
def capital_manual_performed(
    request: HttpRequest,
    proposal_id: UUID,
) -> HttpResponse:
    try:
        movement = _CAPITAL_WORKFLOW.mark_manual_performed(
            proposal_id,
            actor=request.user.get_username(),
            performed_at=timezone.now(),
        )
    except (KeyError, PermissionError) as error:
        raise Http404("Capital action not found.") from error
    except CapitalFundingWorkflowConflict as error:
        messages.error(request, f"Capital action was not recorded: {error}.")
        return redirect("capital-approvals")
    except (CapitalFundingWorkflowError, DatabaseError, ValueError):
        messages.error(
            request,
            "The performed action could not be recorded safely. Ledger capital was not changed.",
        )
        return redirect("capital-approvals")

    if movement.state.value == "pending":
        messages.success(
            request,
            "Transfer marked as performed. It is pending reconciliation; ledger capital is unchanged.",
        )
    return redirect("capital-approvals")
