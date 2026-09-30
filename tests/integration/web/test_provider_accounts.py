from __future__ import annotations

from datetime import UTC, datetime

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from qbet.provider_accounts import ProviderAccountSource, ProviderAccountStatus
from qbet.storage.models import SportsbookProviderRow
from qbet.web.models import ProviderAccount
from qbet.web.provider_accounts import PostgresProviderAccountRegistry


class ProviderAccountRegistryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("account-user", password="Strong-pass-123")
        self.other = User.objects.create_user("account-other", password="Strong-pass-123")
        self.provider = SportsbookProviderRow.objects.create(
            provider_id="licensed-book",
            legal_name="Licensed Book GmbH",
            display_name="Licensed Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://example.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )
        self.second = SportsbookProviderRow.objects.create(
            provider_id="second-book",
            legal_name="Second Book GmbH",
            display_name="Second Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://second.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )
        self.registry = PostgresProviderAccountRegistry()

    def test_registry_projects_missing_catalog_provider_as_not_configured(self) -> None:
        accounts = self.registry.list_for_user(user_id=self.user.pk)

        self.assertEqual(
            [(item.provider_name, item.status) for item in accounts],
            [
                ("Licensed Book", ProviderAccountStatus.NOT_CONFIGURED),
                ("Second Book", ProviderAccountStatus.NOT_CONFIGURED),
            ],
        )
        self.assertFalse(accounts[0].is_configured)
        self.assertFalse(accounts[0].is_verified)

    def test_manual_update_is_user_owned_and_explicitly_unverified(self) -> None:
        snapshot = self.registry.set_manual(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
            status=ProviderAccountStatus.ACTIVE,
            nickname="Main account",
            notes="Declared by user",
        )

        row = ProviderAccount.objects.get(user=self.user, provider=self.provider)
        self.assertEqual(row.status, ProviderAccountStatus.ACTIVE.value)
        self.assertEqual(row.source, ProviderAccountSource.MANUAL.value)
        self.assertIsNone(row.last_verified_at)
        self.assertEqual(snapshot.status, ProviderAccountStatus.ACTIVE)
        self.assertTrue(snapshot.is_declared_available)
        self.assertFalse(snapshot.is_verified)
        self.assertEqual(snapshot.nickname, "Main account")

    def test_same_user_provider_duplicate_is_rejected_by_database(self) -> None:
        ProviderAccount.objects.create(
            user=self.user,
            provider=self.provider,
            status=ProviderAccountStatus.DECLARED.value,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProviderAccount.objects.create(
                user=self.user,
                provider=self.provider,
                status=ProviderAccountStatus.ACTIVE.value,
            )

    def test_registry_is_owner_scoped(self) -> None:
        ProviderAccount.objects.create(
            user=self.other,
            provider=self.provider,
            status=ProviderAccountStatus.FROZEN.value,
            nickname="Other user secret nickname",
            notes="Other user private note",
        )

        snapshot = self.registry.get_for_user(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
        )

        self.assertEqual(snapshot.status, ProviderAccountStatus.NOT_CONFIGURED)
        self.assertEqual(snapshot.nickname, "")
        self.assertEqual(snapshot.notes, "")

    def test_manual_status_can_transition_and_return_to_not_configured(self) -> None:
        self.registry.set_manual(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
            status=ProviderAccountStatus.ACTIVE,
        )
        frozen = self.registry.set_manual(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
            status=ProviderAccountStatus.FROZEN,
        )
        removed = self.registry.set_manual(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
            status=ProviderAccountStatus.NOT_CONFIGURED,
        )

        self.assertEqual(frozen.status, ProviderAccountStatus.FROZEN)
        self.assertEqual(removed.status, ProviderAccountStatus.NOT_CONFIGURED)
        self.assertFalse(
            ProviderAccount.objects.filter(user=self.user, provider=self.provider).exists()
        )

    def test_future_verified_adapter_seam_preserves_typed_verification_state(self) -> None:
        verified_at = datetime(2026, 9, 29, 18, 0, tzinfo=UTC)

        snapshot = self.registry.record_verified(
            user_id=self.user.pk,
            provider_id=self.provider.provider_id,
            status=ProviderAccountStatus.ACTIVE,
            source=ProviderAccountSource.API,
            verified_at=verified_at,
        )

        self.assertEqual(snapshot.source, ProviderAccountSource.API)
        self.assertEqual(snapshot.last_verified_at, verified_at)
        self.assertTrue(snapshot.is_verified)
        self.assertTrue(snapshot.is_declared_available)

    def test_manual_source_cannot_be_forged_as_verified_adapter_state(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.record_verified(
                user_id=self.user.pk,
                provider_id=self.provider.provider_id,
                status=ProviderAccountStatus.ACTIVE,
                source=ProviderAccountSource.MANUAL,
            )


class ProviderAccountWebTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("web-account-user", password="Strong-pass-123")
        self.other = User.objects.create_user("web-account-other", password="Strong-pass-123")
        self.provider = SportsbookProviderRow.objects.create(
            provider_id="licensed-book",
            legal_name="Licensed Book GmbH",
            display_name="Licensed Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://example.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )

    def test_provider_account_page_requires_login_and_shows_safe_boundary(self) -> None:
        self.assertRedirects(
            self.client.get("/provider-accounts/"),
            "/accounts/login/?next=/provider-accounts/",
        )
        self.client.force_login(self.user)

        response = self.client.get("/provider-accounts/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sportsbook accounts")
        self.assertContains(response, "Licensed Book")
        self.assertContains(response, "Not configured")
        self.assertContains(response, "Account status is not a balance.")
        self.assertContains(response, "Manual entries are unverified")
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, 'name="cookie"')
        self.assertNotContains(response, 'name="mfa"')
        self.assertNotContains(response, 'name="balance"')

    def test_user_can_save_manual_status_without_setting_verification_source(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            f"/provider-accounts/{self.provider.provider_id}/",
            {
                "status": ProviderAccountStatus.ACTIVE.value,
                "nickname": "Primary",
                "notes": "Manual status only",
            },
        )

        self.assertRedirects(response, "/provider-accounts/")
        row = ProviderAccount.objects.get(user=self.user, provider=self.provider)
        self.assertEqual(row.status, ProviderAccountStatus.ACTIVE.value)
        self.assertEqual(row.source, ProviderAccountSource.MANUAL.value)
        self.assertIsNone(row.last_verified_at)

        page = self.client.get("/provider-accounts/")
        self.assertContains(page, "Active")
        self.assertContains(page, "Primary")
        self.assertContains(page, "Manual · unverified")

    def test_forged_source_field_is_rejected_without_mutation(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            f"/provider-accounts/{self.provider.provider_id}/",
            {
                "status": ProviderAccountStatus.ACTIVE.value,
                "nickname": "",
                "notes": "",
                "source": ProviderAccountSource.API.value,
            },
            follow=True,
        )

        self.assertContains(response, "Provider account details were not accepted.")
        self.assertFalse(ProviderAccount.objects.filter(user=self.user).exists())

    def test_one_user_cannot_read_or_change_another_users_account_state(self) -> None:
        other_row = ProviderAccount.objects.create(
            user=self.other,
            provider=self.provider,
            status=ProviderAccountStatus.FROZEN.value,
            nickname="Other user account",
            notes="Private other-user note",
        )
        self.client.force_login(self.user)

        page = self.client.get("/provider-accounts/")
        self.assertContains(page, "Not configured")
        self.assertNotContains(page, "Other user account")
        self.assertNotContains(page, "Private other-user note")

        self.client.post(
            f"/provider-accounts/{self.provider.provider_id}/",
            {
                "status": ProviderAccountStatus.ACTIVE.value,
                "nickname": "My account",
                "notes": "",
            },
        )

        other_row.refresh_from_db()
        self.assertEqual(other_row.status, ProviderAccountStatus.FROZEN.value)
        self.assertEqual(other_row.nickname, "Other user account")
        self.assertTrue(
            ProviderAccount.objects.filter(
                user=self.user,
                provider=self.provider,
                status=ProviderAccountStatus.ACTIVE.value,
            ).exists()
        )

    def test_not_configured_removes_only_current_users_record(self) -> None:
        mine = ProviderAccount.objects.create(
            user=self.user,
            provider=self.provider,
            status=ProviderAccountStatus.ACTIVE.value,
        )
        other = ProviderAccount.objects.create(
            user=self.other,
            provider=self.provider,
            status=ProviderAccountStatus.ACTIVE.value,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            f"/provider-accounts/{self.provider.provider_id}/",
            {
                "status": ProviderAccountStatus.NOT_CONFIGURED.value,
                "nickname": "",
                "notes": "",
            },
        )

        self.assertRedirects(response, "/provider-accounts/")
        self.assertFalse(ProviderAccount.objects.filter(pk=mine.pk).exists())
        self.assertTrue(ProviderAccount.objects.filter(pk=other.pk).exists())

    def test_settings_links_to_provider_account_registry(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/settings/presentation/")

        self.assertContains(response, 'href="/provider-accounts/"')
        self.assertContains(response, "Manage sportsbook accounts")
