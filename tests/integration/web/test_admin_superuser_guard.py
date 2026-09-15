from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.test import TestCase

from qbet.web.admin import ProtectedUserAdmin, ProtectedUserChangeForm
from qbet.web.admin_guard import last_superuser_message
from qbet.web.forms import NotificationReadyUserCreationForm


class LastActiveSuperuserGuardTests(TestCase):
    def setUp(self) -> None:
        self.admin_user = User.objects.create_superuser(
            "primary-admin",
            "primary@example.test",
            "Strong-pass-123",
        )

    def test_last_active_superuser_cannot_lose_admin_access(self) -> None:
        for field in ("is_active", "is_staff", "is_superuser"):
            with self.subTest(field=field):
                user = User.objects.get(pk=self.admin_user.pk)
                setattr(user, field, False)

                with self.assertRaisesMessage(ValidationError, last_superuser_message()):
                    user.save()

                persisted = User.objects.get(pk=self.admin_user.pk)
                self.assertTrue(persisted.is_active)
                self.assertTrue(persisted.is_staff)
                self.assertTrue(persisted.is_superuser)

    def test_database_guard_blocks_deleting_last_active_superuser(self) -> None:
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                self.admin_user.delete()

        self.assertTrue(User.objects.filter(pk=self.admin_user.pk).exists())

    def test_database_guard_blocks_updates_that_bypass_model_save(self) -> None:
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                User.objects.filter(pk=self.admin_user.pk).update(is_superuser=False)

        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_superuser)

    def test_admin_change_form_rejects_demoting_last_active_superuser(self) -> None:
        form = ProtectedUserChangeForm(
            data={
                "username": self.admin_user.username,
                "first_name": "",
                "last_name": "",
                "email": self.admin_user.email,
                "is_active": "on",
                "is_staff": "on",
            },
            instance=self.admin_user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn(last_superuser_message(), form.non_field_errors())

    def test_admin_add_form_requires_notification_ready_identity(self) -> None:
        registered_admin = admin.site._registry[User]
        self.assertIs(registered_admin.add_form, NotificationReadyUserCreationForm)

        incomplete = registered_admin.add_form(
            data={
                "username": "admin-created-incomplete",
                "password1": "Strong-pass-123",
                "password2": "Strong-pass-123",
            }
        )
        self.assertFalse(incomplete.is_valid())
        self.assertIn("first_name", incomplete.errors)
        self.assertIn("last_name", incomplete.errors)
        self.assertIn("email", incomplete.errors)

        complete = registered_admin.add_form(
            data={
                "username": "admin-created-ready",
                "first_name": "Admin",
                "last_name": "Created",
                "email": "admin-created@example.test",
                "password1": "Strong-pass-123",
                "password2": "Strong-pass-123",
            }
        )
        self.assertTrue(complete.is_valid(), complete.errors)
        created = complete.save()
        self.assertEqual(
            (created.first_name, created.last_name, created.email),
            ("Admin", "Created", "admin-created@example.test"),
        )

    def test_admin_change_form_cannot_degrade_ready_profile_but_legacy_can_remain_unready(
        self,
    ) -> None:
        ready = User.objects.create_user(
            "ready-user",
            password="Strong-pass-123",
            first_name="Ready",
            last_name="User",
            email="ready@example.test",
        )
        degraded = ProtectedUserChangeForm(
            data={
                "username": ready.username,
                "first_name": "Ready",
                "last_name": "User",
                "email": "",
                "is_active": "on",
                "date_joined": ready.date_joined,
            },
            instance=ready,
        )
        self.assertFalse(degraded.is_valid())
        self.assertIn(
            "Notification-ready users must keep first name, last name, and a valid email.",
            degraded.non_field_errors(),
        )

        legacy = User.objects.create_user("legacy-user", password="Strong-pass-123")
        unchanged_legacy = ProtectedUserChangeForm(
            data={
                "username": legacy.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "date_joined": legacy.date_joined,
            },
            instance=legacy,
        )
        self.assertTrue(unchanged_legacy.is_valid(), unchanged_legacy.errors)

    def test_admin_delete_model_rejects_last_active_superuser(self) -> None:
        registered_admin = admin.site._registry[User]
        self.assertIsInstance(registered_admin, ProtectedUserAdmin)

        with self.assertRaisesMessage(PermissionDenied, last_superuser_message()):
            registered_admin.delete_model(None, self.admin_user)

        self.assertTrue(User.objects.filter(pk=self.admin_user.pk).exists())

    def test_second_active_superuser_allows_first_to_be_demoted_or_deleted(self) -> None:
        second = User.objects.create_superuser(
            "secondary-admin",
            "secondary@example.test",
            "Strong-pass-123",
        )

        self.admin_user.is_superuser = False
        self.admin_user.is_staff = False
        self.admin_user.save(update_fields=("is_superuser", "is_staff"))
        self.admin_user.delete()

        second.refresh_from_db()
        self.assertTrue(second.is_active)
        self.assertTrue(second.is_staff)
        self.assertTrue(second.is_superuser)

    def test_django_admin_uses_protected_user_admin_and_is_available(self) -> None:
        registered_admin = admin.site._registry[User]
        self.assertIsInstance(registered_admin, ProtectedUserAdmin)
        self.assertIn(Group, admin.site._registry)

        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/auth/user/").status_code, 200)
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 200)

        qbet_admin_area = self.client.get("/admin-area/")
        self.assertEqual(qbet_admin_area.status_code, 200)
        self.assertContains(qbet_admin_area, 'href="/admin/"')
