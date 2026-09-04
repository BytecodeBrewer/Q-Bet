"""Request correlation and safe structured logging for the web shell."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from time import perf_counter

from django.conf import settings
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("qbet.web.request")
_correlation_id_pattern = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,35}$")
_HOSTED_PREVIEW_PUBLIC_PATHS = frozenset(("/", "/health/"))
_HOSTED_PREVIEW_PUBLIC_PREFIXES = ("/static/",)
_HOSTED_PREVIEW_UNAVAILABLE_MESSAGE = (
    "Q-Bet hosted preview is read-only until persistent cloud storage is configured."
)


class RequestCorrelationMiddleware:
    """Attach a correlation ID and log an intentionally narrow request summary."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = request.headers.get("X-Correlation-ID", "")
        if not _correlation_id_pattern.fullmatch(correlation_id):
            correlation_id = str(uuid.uuid4())
        started_at = perf_counter()
        response = self.get_response(request)
        response["X-Correlation-ID"] = correlation_id
        user = getattr(request, "user", None)
        user_id = (
            str(getattr(user, "pk", None))
            if user is not None and getattr(user, "is_authenticated", False)
            else None
        )
        logger.info(
            "request.completed",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                "user_id": user_id,
                "correlation_id": correlation_id,
            },
        )
        return response


class HostedPreviewBoundaryMiddleware:
    """Fail closed before session/auth middleware can reach persistent state on Vercel."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "QBET_HOSTED_PREVIEW", False):
            return self.get_response(request)

        path = request.path_info
        if path in _HOSTED_PREVIEW_PUBLIC_PATHS or any(
            path.startswith(prefix) for prefix in _HOSTED_PREVIEW_PUBLIC_PREFIXES
        ):
            return self.get_response(request)

        return HttpResponse(
            _HOSTED_PREVIEW_UNAVAILABLE_MESSAGE,
            status=503,
            content_type="text/plain; charset=utf-8",
        )
