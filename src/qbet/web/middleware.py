"""Request correlation and safe structured logging for the web shell."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from time import perf_counter

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("qbet.web.request")
_correlation_id_pattern = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,35}$")


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
        user_id = str(user.pk) if getattr(user, "is_authenticated", False) else None
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
