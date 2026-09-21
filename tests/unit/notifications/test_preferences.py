from qbet.notifications.preferences import NotificationPreferences


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
