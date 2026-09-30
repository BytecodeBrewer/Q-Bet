"""Protected hosted wake-up boundary for bounded manual Execution maintenance."""

from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from qbet.storage.ledger import AuthoritativePersistenceError
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.manual_execution import ManualExecutionService


@csrf_exempt
@require_POST
def execution_tick(request: HttpRequest) -> JsonResponse:
    """Reconcile a bounded batch of expired manual Execution actions."""

    token = settings.QBET_EXECUTION_TICK_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not token or not constant_time_compare(supplied, f"Bearer {token}"):
        return JsonResponse({"detail": "Not found."}, status=404)

    now = timezone.now()
    limit = settings.QBET_EXECUTION_TICK_MAX_WORK
    try:
        expired_approvals = ExecutionApprovalService().expire_due(
            now=now,
            limit=limit,
        )
        remaining = limit - len(expired_approvals)
        expired_manual_actions = (
            ManualExecutionService().expire_due(
                now=now,
                limit=remaining,
            )
            if remaining > 0
            else ()
        )
    except (AuthoritativePersistenceError, DatabaseError, ValueError):
        return JsonResponse(
            {"status": "unavailable", "reason": "execution_runtime_unavailable"},
            status=503,
        )

    return JsonResponse(
        {
            "status": "ok",
            "processed": len(expired_approvals) + len(expired_manual_actions),
            "expired_approvals": len(expired_approvals),
            "expired_manual_actions": len(expired_manual_actions),
        }
    )
