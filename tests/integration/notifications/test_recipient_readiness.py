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
CORRELATION_ID = UUID("22345678-1234-5678-1234-567812345678")


def _create_user(**values: str) -> Any:
    return cast(Any, get_user_model().objects).create_user(**values)


def _request(opportunity_id: str) -> BonusEngineRequest:
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


def _coordinator(opportunity_id: str) -> ModeDispatchCoordinator:
    return ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        queue_repository=ModeWorkQueueRepository(),
        mode_request_handlers=ModeRequestHandlers(
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
                        result_reference="readiness-result",
                    ),
                ),
            ),
        ),
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificationRecipientReadinessIntegrationTests(TransactionTestCase):
    def test_legacy_invalid_email_is_typed_failed_after_revalidation_without_delivery(self) -> None:
        user = _create_user(
            username="legacy-invalid-email",
            password="Strong-pass-123",
            email="not-an-email",
        )
        opportunity_id = "legacy-readiness-opportunity"
        dispatcher = _coordinator(opportunity_id)
        (scheduled,) = dispatcher.schedule(
            _request(opportunity_id),
            owner=user.get_username(),
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        dispatcher.dispatch_due(now=NOW, owner=user.get_username())

        self.client.force_login(user)
        response = self.client.post(
            reverse("execution-approval-decision", args=[scheduled.work.id]),
            {"decision": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(NotificationTaskRow.objects.count(), 0)

        queued = ModeWorkQueueRepository().load(scheduled.work.id)
        assert queued is not None
        dispatcher.dispatch_due(
            now=queued.scheduled_for + timedelta(seconds=1),
            owner=user.get_username(),
        )

        task = NotificationTaskRow.objects.get()
        self.assertEqual(task.state, "failed")
        self.assertEqual(task.payload["failure_reason"], "recipient_email_invalid")
        self.assertEqual(task.recipient_id, user.get_username())
        self.assertEqual(len(getattr(mail, "outbox", [])), 0)
