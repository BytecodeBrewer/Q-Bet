"""Safe navigation projection for the authenticated approval inbox."""

from __future__ import annotations

from django.db import DatabaseError
from django.http import HttpRequest

from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.workflow.approval import ExecutionApprovalService

_APPROVALS = ExecutionApprovalService()


def approval_navigation(request: HttpRequest) -> dict[str, object]:
    """Expose only the active owner-scoped count and never break unrelated pages."""

    if not request.user.is_authenticated:
        return {
            "approval_count": 0,
            "approval_count_available": True,
        }

    try:
        count = _APPROVALS.active_count_for(request.user.get_username())
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return {
            "approval_count": 0,
            "approval_count_available": False,
        }

    return {
        "approval_count": count,
        "approval_count_available": True,
    }
