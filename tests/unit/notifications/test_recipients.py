from qbet.notifications import notification_recipient_status


def test_recipient_status_requires_a_valid_email_and_uses_a_safe_name_fallback() -> None:
    missing = notification_recipient_status(user_id="owner", first_name="", last_name="", email="")
    invalid = notification_recipient_status(
        user_id="owner", first_name="First", last_name="Last", email="not-an-email"
    )
    ready = notification_recipient_status(
        user_id="owner", first_name="First", last_name="Last", email="owner@example.com"
    )

    assert (missing.ready, missing.display_name, missing.reason_code) == (
        False,
        "owner",
        "recipient_email_missing",
    )
    assert (invalid.ready, invalid.reason_code) == (False, "recipient_email_invalid")
    assert (ready.ready, ready.display_name, ready.email) == (
        True,
        "First Last",
        "owner@example.com",
    )
