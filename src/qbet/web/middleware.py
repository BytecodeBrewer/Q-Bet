"""Request correlation and safe structured logging for the web shell."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from time import perf_counter
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("qbet.web.request")
_correlation_id_pattern = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,35}$")
_secret_path_patterns = (
    re.compile(r"^/verify-email/[^/]+/[^/]+/?$"),
    re.compile(r"^/accounts/password/reset/[^/]+/[^/]+/?$"),
)


def safe_request_path(path: str) -> str:
    """Redact one-time account tokens before structured request logging."""

    if _secret_path_patterns[0].match(path):
        return "/verify-email/<redacted>/"
    if _secret_path_patterns[1].match(path):
        return "/accounts/password/reset/<redacted>/"
    return path


class RequestCorrelationMiddleware:
    """Attach a correlation ID and log an intentionally narrow request summary."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = request.headers.get("X-Correlation-ID", "")
        if not _correlation_id_pattern.fullmatch(correlation_id):
            correlation_id = str(uuid.uuid4())
        started_at = perf_counter()
        query_count = 0
        query_duration_ms = 0.0

        if settings.QBET_PROFILE_WEB_REQUESTS:
            def profile_execute(
                execute: Callable[..., Any],
                sql: str,
                params: object,
                many: bool,
                context: object,
            ) -> Any:
                nonlocal query_count, query_duration_ms
                query_started_at = perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    query_count += 1
                    query_duration_ms += (perf_counter() - query_started_at) * 1000

            with connection.execute_wrapper(profile_execute):
                response = self.get_response(request)
        else:
            response = self.get_response(request)

        response["X-Correlation-ID"] = correlation_id
        user = getattr(request, "user", None)
        user_id = (
            str(getattr(user, "pk", None))
            if user is not None and getattr(user, "is_authenticated", False)
            else None
        )
        extra: dict[str, object] = {
            "method": request.method,
            "path": safe_request_path(request.path),
            "status": response.status_code,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "user_id": user_id,
            "correlation_id": correlation_id,
        }
        if settings.QBET_PROFILE_WEB_REQUESTS:
            extra.update(
                query_count=query_count,
                query_duration_ms=round(query_duration_ms, 3),
            )
        logger.info("request.completed", extra=extra)
        return response


class BrowserSecurityHeadersMiddleware:
    """Add browser policy headers that Django does not provide as one setting."""

    _CSP = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'"
    )
    _PERMISSIONS_POLICY = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self._CSP)
        response.setdefault("Permissions-Policy", self._PERMISSIONS_POLICY)
        return response
