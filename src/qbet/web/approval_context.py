"""Safe navigation projection for the authenticated approval inbox."""

from __future__ import annotations

from django.db import DatabaseError
from django.http import HttpRequest

from qbet.notifications import PostgresNotificationInbox
from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.web.simulation_control import SimulationControlService
from qbet.workflow.approval import ExecutionApprovalService

_APPROVALS = ExecutionApprovalService()
_NOTIFICATION_INBOX = PostgresNotificationInbox()
_SIMULATION_CONTROL = SimulationControlService()


def approval_navigation(request: HttpRequest) -> dict[str, object]:
    """Expose only the active owner-scoped count and never break unrelated pages."""

    if not request.user.is_authenticated:
        return {
            "approval_count": 0,
            "approval_count_available": True,
            "notification_unread_count": 0,
            "simulation_enabled": False,
        }

    simulation_enabled = False
    if request.user.is_staff:
        try:
            simulation_enabled = _SIMULATION_CONTROL.availability().enabled
        except DatabaseError:
            simulation_enabled = False

    try:
        count = _APPROVALS.active_count_for(request.user.get_username())
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return {
            "approval_count": 0,
            "approval_count_available": False,
            "notification_unread_count": sum(
                not item.read for item in _NOTIFICATION_INBOX.list(request.user.get_username())
            ),
            "simulation_enabled": simulation_enabled,
        }

    inbox = _NOTIFICATION_INBOX.list(request.user.get_username())
    return {
        "approval_count": count,
        "approval_count_available": True,
        "notification_unread_count": sum(not item.read for item in inbox),
        "simulation_enabled": simulation_enabled,
    }
