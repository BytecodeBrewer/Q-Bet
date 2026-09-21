"""Safe navigation projection for the authenticated approval inbox."""

from __future__ import annotations

from django.db import DatabaseError
from django.http import HttpRequest

from qbet.notifications import PostgresNotificationInbox
from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.workflow.approval import ExecutionApprovalService

_APPROVALS = ExecutionApprovalService()
_NOTIFICATION_INBOX = PostgresNotificationInbox()


def approval_navigation(request: HttpRequest) -> dict[str, object]:
    """Expose only the active owner-scoped count and never break unrelated pages."""

    if not request.user.is_authenticated:
        return {
            "approval_count": 0,
            "approval_count_available": True,
            "notification_unread_count": 0,
        }

    try:
        count = _APPROVALS.active_count_for(request.user.get_username())
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return {
            "approval_count": 0,
            "approval_count_available": False,
            "notification_unread_count": sum(
                not item.read for item in _NOTIFICATION_INBOX.list(request.user.get_username())
            ),
        }

    inbox = _NOTIFICATION_INBOX.list(request.user.get_username())
    return {
        "approval_count": count,
        "approval_count_available": True,
        "notification_unread_count": sum(not item.read for item in inbox),
    }
