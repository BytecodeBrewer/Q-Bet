from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)
from qbet.storage.ledger import ModeWorkQueueRepository
from qbet.storage.models import NotificationTaskRow
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration

NOW = datetime.now(UTC).replace(microsecond=0)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def request(opportunity_id: str) -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=opportunity_id,
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("bookmaker", "exchange"),
        generated_at=NOW,
    )


def coordinator(opportunity_id: str) -> ModeDispatchCoordinator:
    handlers = ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(),
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(
                SandboxRevalidationFixture(
                    opportunity_id=opportunity_id,
                    outcome=RevalidationOutcome.VALID,
                    validated_at=NOW,
                ),
            ),
            result_fixtures=(
                SandboxResultFixture(
                    opportunity_id=opportunity_id,
                    status=ResultStatus.SUCCESS,
                    observed_at=NOW,
                    result_reference="notification-result",
                ),
            ),
        ),
    )
    return ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        queue_repository=ModeWorkQueueRepository(),
        mode_request_handlers=handlers,
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ExecutionEmailIntegrationTests(TransactionTestCase):
    def stage(self, *, owner: str, opportunity_id: str):
        engine_request = request(opportunity_id)
        dispatcher = coordinator(opportunity_id)
        (scheduled,) = dispatcher.schedule(
            engine_request,
            owner=owner,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        dispatcher.dispatch_due(now=NOW, owner=owner)
        return scheduled

    def test_web_approval_sends_customer_safe_email_once(self) -> None:
        user = get_user_model().objects.create_user(
            username="alice",
            password="test-password-123",
            email="alice@example.com",
            first_name="Alice",
            last_name="Example",
        )
        scheduled = self.stage(owner=user.get_username(), opportunity_id="email-opportunity")
        self.client.force_login(user)
        url = reverse("execution-approval-decision", args=[scheduled.work.id])

        first = self.client.post(url, {"decision": "approve"})
        repeated = self.client.post(url, {"decision": "approve"})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(NotificationTaskRow.objects.count(), 1)
        task = NotificationTaskRow.objects.get()
        self.assertEqual(task.state, "sent")
        self.assertEqual(task.recipient_id, "alice")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["alice@example.com"])
        self.assertIn("Hello Alice Example", message.body)
        self.assertIn("email-opportunity", message.body)
        self.assertIn("qualifying_bet", message.body)
        self.assertIn("bookmaker", message.body)
        self.assertIn("10", message.body)
        self.assertIn(str(CORRELATION_ID), message.body)
        self.assertNotIn("pipeline", message.body.lower())
        self.assertNotIn("recheck", message.body.lower())
        self.assertNotIn("token", message.body.lower())

    def test_username_fallback_is_used_when_name_is_missing(self) -> None:
        user = get_user_model().objects.create_user(
            username="fallback-user",
            password="test-password-123",
            email="fallback@example.com",
        )
        scheduled = self.stage(
            owner=user.get_username(),
            opportunity_id="fallback-opportunity",
        )
        self.client.force_login(user)

        self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Hello fallback-user", mail.outbox[0].body)

    def test_missing_email_is_persisted_as_failed_without_delivery(self) -> None:
        user = get_user_model().objects.create_user(
            username="no-email",
            password="test-password-123",
        )
        scheduled = self.stage(owner=user.get_username(), opportunity_id="missing-email")
        self.client.force_login(user)

        response = self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )

        self.assertEqual(response.status_code, 302)
        task = NotificationTaskRow.objects.get()
        self.assertEqual(task.state, "failed")
        self.assertEqual(task.payload["failure_reason"], "recipient_email_missing")
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)
