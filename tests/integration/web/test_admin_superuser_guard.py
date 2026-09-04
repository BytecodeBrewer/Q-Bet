from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import TestCase

from qbet.web.admin import ProtectedUserAdmin, ProtectedUserChangeForm
from qbet.web.admin_guard import last_superuser_message


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

    def test_last_active_superuser_cannot_be_deleted(self) -> None:
        with self.assertRaisesMessage(ValidationError, last_superuser_message()):
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
