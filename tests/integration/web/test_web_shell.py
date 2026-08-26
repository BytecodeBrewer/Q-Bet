from __future__ import annotations

import io
import json
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

from django.conf import settings
from django.test import Client, SimpleTestCase

from qbet.web.logging import SafeRequestJSONFormatter
from qbet.web.settings import parse_allowed_hosts


class WebShellSmokeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_health_route_returns_small_json_status(self) -> None:
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "q-bet-web"})

    def test_home_renders_static_engine_statuses_without_domain_calculations(self) -> None:
        response = self.client.get("/")

        self.assertContains(response, "Q-Bet")
        self.assertContains(response, "Base")
        self.assertContains(response, "Yield")
        self.assertContains(response, "Alpha")

    def test_account_boundary_requires_django_authentication(self) -> None:
        response = self.client.get("/account/")

        self.assertRedirects(response, "/accounts/login/?next=/account/", fetch_redirect_response=False)

    def test_security_and_csrf_middleware_are_enabled(self) -> None:
        self.assertIn("django.middleware.security.SecurityMiddleware", settings.MIDDLEWARE)
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", settings.MIDDLEWARE)

        response = self.client.get("/accounts/login/")
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_correlation_id_is_propagated_and_log_excludes_secret_bearing_data(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeRequestJSONFormatter())
        request_logger = logging.getLogger("qbet.web.request")
        request_logger.addHandler(handler)
        correlation_id = "123e4567-e89b-12d3-a456-426614174000"
        try:
            response = self.client.get(
                "/health/?password=not-for-logs",
                HTTP_X_CORRELATION_ID=correlation_id,
                HTTP_AUTHORIZATION="Bearer not-for-logs",
                HTTP_X_BANK_DETAILS="not-for-logs",
                HTTP_X_BOOKMAKER_CREDENTIAL="not-for-logs",
            )
        finally:
            request_logger.removeHandler(handler)

        payload = json.loads(stream.getvalue())
        self.assertEqual(response["X-Correlation-ID"], correlation_id)
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["path"], "/health/")
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["correlation_id"], correlation_id)
        self.assertIn("duration_ms", payload)
        self.assertNotIn("password", stream.getvalue())
        self.assertNotIn("authorization", stream.getvalue().lower())
        self.assertNotIn("cookie", stream.getvalue().lower())
        self.assertNotIn("bank", stream.getvalue().lower())
        self.assertNotIn("bookmaker", stream.getvalue().lower())


def test_parses_comma_separated_allowed_hosts_without_whitespace() -> None:
    assert parse_allowed_hosts("example.com, www.example.com, , api.example.com ") == [
        "example.com",
        "www.example.com",
        "api.example.com",
    ]


def test_invalid_host_returns_400_with_correlation_id_before_authentication() -> None:
    correlation_id = "123e4567-e89b-12d3-a456-426614174001"

    response = Client().get(
        "/health/",
        HTTP_HOST="unapproved.example",
        HTTP_X_CORRELATION_ID=correlation_id,
    )

    assert response.status_code == 400
    assert response["X-Correlation-ID"] == correlation_id