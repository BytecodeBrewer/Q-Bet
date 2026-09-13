from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
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


def create_user(**values: str) -> Any:
    return cast(Any, get_user_model().objects).create_user(**values)


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


def coordinator(
    opportunity_id: str,
    *,
    revalidation_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
) -> ModeDispatchCoordinator:
    handlers = ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(),
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(
                SandboxRevalidationFixture(
                    opportunity_id=opportunity_id,
                    outcome=revalidation_outcome,
                    validated_at=NOW,
                    reason_code=(
                        None
                        if revalidation_outcome is RevalidationOutcome.VALID
                        else "fixture_revalidation"
                    ),
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


def dispatch_after_approval(
    dispatcher: ModeDispatchCoordinator,
    *,
    work_id: UUID,
    owner: str,
):
    queued = ModeWorkQueueRepository().load(work_id)
    assert queued is not None
    return dispatcher.dispatch_due(
        now=queued.scheduled_for + timedelta(seconds=1),
        owner=owner,
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ExecutionEmailIntegrationTests(TransactionTestCase):
    def stage(
        self,
        *,
        owner: str,
        opportunity_id: str,
        revalidation_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    ):
        engine_request = request(opportunity_id)
        dispatcher = coordinator(opportunity_id, revalidation_outcome=revalidation_outcome)
        (scheduled,) = dispatcher.schedule(
            engine_request,
            owner=owner,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        dispatcher.dispatch_due(now=NOW, owner=owner)
        return scheduled, dispatcher

    def test_successful_final_revalidation_sends_customer_safe_email_once(self) -> None:
        user = create_user(
            username="alice",
            password="test-password-123",
            email="alice@example.com",
            first_name="Alice",
            last_name="Example",
        )
        scheduled, dispatcher = self.stage(
            owner=user.get_username(), opportunity_id="email-opportunity"
        )
        self.client.force_login(user)
        url = reverse("execution-approval-decision", args=[scheduled.work.id])

        first = self.client.post(url, {"decision": "approve"})
        repeated = self.client.post(url, {"decision": "approve"})

        self.assertEqual(NotificationTaskRow.objects.count(), 0)
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)
        dispatch_after_approval(
            dispatcher,
            work_id=scheduled.work.id,
            owner=user.get_username(),
        )

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
        user = create_user(
            username="fallback-user",
            password="test-password-123",
            email="fallback@example.com",
        )
        scheduled, dispatcher = self.stage(
            owner=user.get_username(),
            opportunity_id="fallback-opportunity",
        )
        self.client.force_login(user)

        self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )

        dispatch_after_approval(
            dispatcher,
            work_id=scheduled.work.id,
            owner=user.get_username(),
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Hello fallback-user", mail.outbox[0].body)

    def test_missing_email_is_persisted_as_failed_without_delivery(self) -> None:
        user = create_user(
            username="no-email",
            password="test-password-123",
        )
        scheduled, dispatcher = self.stage(
            owner=user.get_username(), opportunity_id="missing-email"
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )

        dispatch_after_approval(
            dispatcher,
            work_id=scheduled.work.id,
            owner=user.get_username(),
        )

        self.assertEqual(response.status_code, 302)
        task = NotificationTaskRow.objects.get()
        self.assertEqual(task.state, "failed")
        self.assertEqual(task.payload["failure_reason"], "recipient_email_missing")
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)

    def test_failed_final_revalidation_sends_no_notification(self) -> None:
        user = create_user(
            username="revalidation-failed",
            password="test-password-123",
            email="revalidation-failed@example.com",
        )
        scheduled, _ = self.stage(
            owner=user.get_username(),
            opportunity_id="revalidation-failed-opportunity",
        )
        self.client.force_login(user)

        self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )
        outcome = dispatch_after_approval(
            coordinator(
                "revalidation-failed-opportunity",
                revalidation_outcome=RevalidationOutcome.REJECTED,
            ),
            work_id=scheduled.work.id,
            owner=user.get_username(),
        )

        self.assertEqual(outcome[0].state.value, "cancelled")
        self.assertEqual(NotificationTaskRow.objects.count(), 0)
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)
