from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.notifications.models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationRecipient,
    NotificationStatus,
)
from qbet.notifications.preferences import PostgresNotificationInbox
from qbet.storage.models import NotificationTaskRow
from qbet.web.models import UserDisplayPreference

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
TASK_ID = UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
EXECUTION_ID = UUID("bbbbbbbb-1111-2222-3333-cccccccccccc")
CORRELATION_ID = UUID("cccccccc-1111-2222-3333-dddddddddddd")


class NotificationDisplayPreferenceTests(TestCase):
    def test_notification_inbox_uses_persisted_time_and_currency_preferences(self) -> None:
        user = User.objects.create_user("notification-display", password="Strong-pass-123")
        UserDisplayPreference.objects.create(
            user=user,
            language="de",
            region="DE",
            timezone_name="Europe/Berlin",
            time_format="24h",
            currency="USD",
        )
        task = ExecutionNotificationTask(
            id=TASK_ID,
            execution_id=EXECUTION_ID,
            correlation_id=CORRELATION_ID,
            recipient=NotificationRecipient(
                user_id=user.get_username(),
                email="owner@example.test",
                display_name="Owner",
            ),
            opportunity_id="display-opportunity",
            engine="BonusEngine",
            strategy="qualifying_bet",
            instructions=(
                ExecutionNotificationInstruction(
                    provider="bookmaker",
                    offer_id="offer",
                    amount="10",
                    currency="EUR",
                ),
            ),
            action_starts_at=NOW,
            action_deadline=NOW + timedelta(minutes=30),
            created_at=NOW,
            lifecycle_at=NOW,
            status=NotificationStatus.SENT,
            sent_at=NOW,
            category="execution_action_required",
            email_requested=False,
            inbox_requested=True,
        )
        NotificationTaskRow.objects.create(
            task_id=task.id,
            execution_id=task.execution_id,
            correlation_id=task.correlation_id,
            recipient_id=user.get_username(),
            state=task.status.value,
            payload=task.model_dump(mode="json"),
        )
        self.assertTrue(
            PostgresNotificationInbox().deliver(
                user.get_username(),
                task.id,
                task.category,
            )
        )
        self.client.force_login(user)

        response = self.client.get("/notifications/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "21.09.2026 12:00")
        self.assertContains(response, "Preferred recorded currency: USD.")
        self.assertContains(response, "authoritative recorded currency")
        self.assertNotContains(response, "Sept.")
