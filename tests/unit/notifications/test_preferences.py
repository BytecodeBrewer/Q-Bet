from qbet.notifications import (
    CaptureEmailTransport,
    ExecutionNotificationService,
    InMemoryNotificationRepository,
)
from qbet.notifications.preferences import NotificationPreferences

from .test_service import NOW, approved_record, recipient


def test_notification_preferences_keep_email_and_inbox_enabled_by_default() -> None:
    preferences = NotificationPreferences()

    assert preferences.email_enabled
    assert preferences.inbox_enabled
    assert preferences.accepts("execution_action_required")


def test_notification_preferences_can_disable_all_customer_delivery_channels() -> None:
    preferences = NotificationPreferences(email_enabled=False, inbox_enabled=False, categories=())

    assert not preferences.email_enabled
    assert not preferences.inbox_enabled
    assert not preferences.accepts("execution_action_required")


class PreferenceRepository:
    def __init__(self, preferences: NotificationPreferences) -> None:
        self._preferences = preferences

    def load(self, user_id: str) -> NotificationPreferences:
        return self._preferences

    def save(self, user_id: str, preferences: NotificationPreferences) -> NotificationPreferences:
        self._preferences = preferences
        return preferences


def test_category_preference_suppresses_email_without_changing_execution_state() -> None:
    preferences = NotificationPreferences(categories=())
    transport = CaptureEmailTransport()
    record, queued = approved_record()
    outcome = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=transport,
        preference_repository=PreferenceRepository(preferences),
    ).notify(record, queued, recipient(), now=NOW)

    assert outcome.accepted
    assert outcome.task is not None
    assert outcome.task.status.value == "queued"
    assert not transport.messages
    assert record.state.value == "approved"
