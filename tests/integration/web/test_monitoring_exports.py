from datetime import UTC, datetime
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase

from qbet.monitoring import MonitoringLevel, MonitoringQuery, MonitoringRecord, MonitoringService
from qbet.storage.monitoring import PostgresMonitoringRepository


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href is not None:
            self.hrefs.append(href)


class _UnavailableReader:
    def list_records(self, query: MonitoringQuery) -> tuple[MonitoringRecord, ...]:
        del query
        raise OSError("monitoring history is unavailable")


class MonitoringExportTests(TestCase):
    def setUp(self) -> None:
        self.staff = get_user_model().objects.create_user("staff", password="test", is_staff=True)
        self.user = get_user_model().objects.create_user("user", password="test")
        self.correlation_id = UUID("12345678-1234-5678-1234-567812345678")
        PostgresMonitoringRepository().append(
            MonitoringRecord(
                correlation_id=self.correlation_id,
                occurred_at=datetime(2026, 9, 7, 10, tzinfo=UTC),
                engine="sports_capital",
                mode="execution",
                stage="liquidity_check",
                event_type="stage",
                status="recheck",
                level=MonitoringLevel.WARNING,
                reason_code="provider_delayed",
                duration_ms=25,
                references={
                    "credential": "never-store-this",
                    "work_id": "work-1",
                    "opportunity_id": "match-1",
                },
            )
        )
        PostgresMonitoringRepository().append(
            MonitoringRecord(
                correlation_id=UUID("87654321-4321-8765-4321-876543218765"),
                occurred_at=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
                engine="bonus",
                mode="simulation",
                stage="calculation",
                event_type="stage",
                status="allow",
                references={"opportunity_id": "match-2"},
            )
        )

    @staticmethod
    def _range_params() -> dict[str, str]:
        return {"start": "2026-09-07T09:00:00+00:00", "end": "2026-09-07T11:00:00+00:00"}

    @staticmethod
    def _rendered_links(response) -> list[tuple[str, dict[str, list[str]]]]:
        parser = _HrefCollector()
        parser.feed(response.content.decode())
        return [(urlsplit(href).path, parse_qs(urlsplit(href).query)) for href in parser.hrefs]

    def test_staff_can_view_compact_and_extended_monitoring_with_diagnostics(self) -> None:
        self.client.force_login(self.staff)
        compact = self.client.get("/monitoring/", self._range_params())
        extended = self.client.get("/monitoring/", {"view": "extended", **self._range_params()})

        self.assertContains(compact, "sports_capital")
        self.assertContains(compact, "match-1")
        self.assertContains(compact, "provider_delayed")
        self.assertContains(compact, 'name="start"')
        self.assertContains(compact, 'name="end"')
        self.assertContains(compact, 'name="correlation"')
        self.assertContains(compact, 'aria-label="Monitoring filters"')
        self.assertContains(extended, "liquidity_check")
        self.assertContains(extended, "warning")
        self.assertContains(extended, "25 ms")
        self.assertContains(extended, "work-1")
        self.assertContains(extended, "[redacted]")
        self.assertNotContains(extended, "never-store-this")

    def test_staff_can_submit_local_datetime_filter_values(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.get(
            "/monitoring/",
            {"start": "2026-09-07T09:00", "end": "2026-09-07T11:00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(response, "match-1")

    def test_selected_range_and_correlation_survive_view_switch_and_exports(self) -> None:
        self.client.force_login(self.staff)
        params = {
            "view": "extended",
            "correlation": str(self.correlation_id),
            **self._range_params(),
        }
        response = self.client.get("/monitoring/", params)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "match-1")
        self.assertNotContains(response, "match-2")
        self.assertContains(response, str(self.correlation_id))

        expected_filters = {
            "correlation": [str(self.correlation_id)],
            "start": [self._range_params()["start"]],
            "end": [self._range_params()["end"]],
        }
        rendered_links = self._rendered_links(response)
        expected_targets = (
            ("", "compact"),
            ("", "extended"),
            ("/monitoring/export/csv/", "extended"),
            ("/monitoring/export/json/", "extended"),
        )
        for path, view in expected_targets:
            with self.subTest(path=path, view=view):
                query = next(
                    query
                    for link_path, query in rendered_links
                    if link_path == path and query.get("view") == [view]
                )
                for name, expected in expected_filters.items():
                    self.assertEqual(query.get(name), expected)

        csv_export = self.client.get("/monitoring/export/csv/", params)
        json_export = self.client.get("/monitoring/export/json/", params)
        self.assertEqual(csv_export.status_code, 200)
        self.assertEqual(json_export.status_code, 200)
        self.assertIn("match-1", csv_export.content.decode())
        self.assertNotIn("match-2", csv_export.content.decode())
        self.assertIn("match-1", json_export.content.decode())
        self.assertNotIn("match-2", json_export.content.decode())
        self.assertIn("[redacted]", json_export.content.decode())

    def test_empty_period_returns_clear_page_and_valid_empty_exports(self) -> None:
        self.client.force_login(self.staff)
        params = {"start": "2026-09-08T09:00:00+00:00", "end": "2026-09-08T10:00:00+00:00"}
        page = self.client.get("/monitoring/", params)
        csv_export = self.client.get("/monitoring/export/csv/", params)
        json_export = self.client.get("/monitoring/export/json/", params)

        self.assertContains(page, "No monitoring records exist in this period.")
        self.assertEqual(csv_export.status_code, 200)
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(json_export.json(), [])
        self.assertEqual(len(csv_export.content.decode().strip().splitlines()), 1)

    def test_unavailable_history_exports_are_explicitly_unavailable(self) -> None:
        self.client.force_login(self.staff)
        unavailable = MonitoringService(_UnavailableReader())

        with patch("qbet.web.views.WORKFLOW_MONITORING_SERVICE", unavailable):
            json_export = self.client.get("/monitoring/export/json/", self._range_params())
            csv_export = self.client.get("/monitoring/export/csv/", self._range_params())

        self.assertEqual(json_export.status_code, 503)
        self.assertEqual(
            json_export.json(),
            {
                "error": "monitoring_history_unavailable",
                "message": "Monitoring history is temporarily unavailable.",
            },
        )
        self.assertEqual(csv_export.status_code, 503)
        self.assertEqual(csv_export["Content-Type"], "text/csv; charset=utf-8")
        csv_body = csv_export.content.decode()
        self.assertIn("monitoring_history_unavailable", csv_body)
        self.assertIn("Monitoring history is temporarily unavailable.", csv_body)

    def test_invalid_monitoring_filters_are_rejected_without_sensitive_detail(self) -> None:
        self.client.force_login(self.staff)
        invalid_queries = (
            {"start": "not-a-date", "end": "2026-09-07T11:00:00+00:00"},
            {"start": "2026-09-07T12:00:00+00:00", "end": "2026-09-07T11:00:00+00:00"},
            {"start": "2026-08-01T09:00:00+00:00", "end": "2026-09-07T11:00:00+00:00"},
            {"correlation": "not-a-uuid", **self._range_params()},
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/monitoring/", query).status_code, 404)
                self.assertEqual(
                    self.client.get("/monitoring/export/json/", query).status_code, 404
                )

    def test_normal_user_cannot_read_or_export_monitoring(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/monitoring/").status_code, 302)
        self.assertEqual(self.client.get("/monitoring/export/json/").status_code, 302)
        self.assertEqual(self.client.get("/monitoring/export/csv/").status_code, 302)
