from django.test import SimpleTestCase, TestCase, override_settings


class PollingTickAuthorizationTests(SimpleTestCase):
    def test_tick_is_not_routable_without_a_configured_token(self) -> None:
        response = self.client.post("/internal/polling/tick/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content.decode(), "Not found.")

    @override_settings(QBET_POLLING_TICK_TOKEN="fake-tick-secret")
    def test_tick_rejects_wrong_bearer_without_echoing_secret(self) -> None:
        response = self.client.post(
            "/internal/polling/tick/",
            HTTP_AUTHORIZATION="Bearer incorrect",
        )

        body = response.content.decode()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(body, "Not found.")
        self.assertNotIn("fake-tick-secret", body)


class PollingTickEndpointTests(TestCase):
    @override_settings(
        QBET_POLLING_TICK_TOKEN="fake-tick-secret",
        QBET_POLLING_TICK_MAX_WORK=10,
        QBET_POLLING_ODDS_SPORT="soccer_epl",
        QBET_POLLING_ODDS_EVENT_ID="event-207",
        QBET_POLLING_ODDS_MARKET="h2h",
        QBET_POLLING_ODDS_EVENT_STARTS_AT="2026-09-29T14:00:00Z",
    )
    def test_authorized_tick_is_bounded_and_safe_without_eligible_routes(self) -> None:
        response = self.client.post(
            "/internal/polling/tick/",
            HTTP_AUTHORIZATION="Bearer fake-tick-secret",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["eligible_routes"], 0)
        self.assertEqual(payload["claimed"], 0)
        self.assertNotIn("fake-tick-secret", response.content.decode())

    @override_settings(
        QBET_POLLING_TICK_TOKEN="fake-tick-secret",
        QBET_POLLING_ODDS_SPORT="soccer_epl",
        QBET_POLLING_ODDS_EVENT_ID="event-207",
        QBET_POLLING_ODDS_MARKET="h2h",
        QBET_POLLING_ODDS_EVENT_STARTS_AT="",
    )
    def test_authorized_tick_fails_closed_for_incomplete_target_configuration(self) -> None:
        response = self.client.post(
            "/internal/polling/tick/",
            HTTP_AUTHORIZATION="Bearer fake-tick-secret",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "configuration_unavailable"})
        self.assertNotIn("fake-tick-secret", response.content.decode())
