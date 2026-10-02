"""Protected Preview-only boundary for authenticated release-candidate smoke checks."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.http import HttpRequest, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

_SMOKE_USERNAME = "qbet-preview-smoke"
_SMOKE_SESSION_SECONDS = 300


def _authorized(request: HttpRequest) -> bool:
    if not settings.QBET_HOSTED_PREVIEW:
        return False
    token = settings.QBET_PREVIEW_SMOKE_TOKEN
    supplied = request.headers.get("Authorization", "")
    return bool(token) and constant_time_compare(supplied, f"Bearer {token}")


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def preview_smoke_session(request: HttpRequest) -> JsonResponse:
    """Create or clear one short-lived low-privilege Preview smoke session."""

    if not _authorized(request):
        return JsonResponse({"detail": "Not found."}, status=404)

    if request.method == "DELETE":
        logout(request)
        return JsonResponse({"status": "cleared"})

    try:
        user = User.objects.get(username=_SMOKE_USERNAME)
    except User.DoesNotExist:
        return JsonResponse(
            {"status": "unavailable", "reason": "preview_smoke_identity_unavailable"},
            status=503,
        )

    if not user.is_active or user.is_staff or user.is_superuser:
        return JsonResponse(
            {"status": "unavailable", "reason": "preview_smoke_identity_invalid"},
            status=503,
        )

    login(
        request,
        user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    request.session.set_expiry(_SMOKE_SESSION_SECONDS)
    return JsonResponse({"status": "ok"})
