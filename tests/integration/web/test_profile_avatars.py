from __future__ import annotations

from io import BytesIO
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from PIL import Image

from qbet.web.avatars import AvatarStorageError
from qbet.web.models import UserAvatar


class MemoryAvatarStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, object_key: str, content: bytes) -> None:
        self.objects[object_key] = content

    def download(self, object_key: str) -> bytes:
        if object_key not in self.objects:
            raise AvatarStorageError("missing")
        return self.objects[object_key]

    def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)


def _image_upload(color: tuple[int, int, int]) -> SimpleUploadedFile:
    output = BytesIO()
    Image.new("RGB", (32, 32), color=color).save(output, format="PNG")
    return SimpleUploadedFile("avatar.png", output.getvalue(), content_type="image/png")


class ProfileAvatarTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "avatar-user", first_name="Avatar", last_name="User", password="test-password"
        )
        self.storage = MemoryAvatarStorage()
        self.storage_patch = patch("qbet.web.views.get_avatar_storage", return_value=self.storage)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.avatar_storage_patch = patch(
            "qbet.web.avatars.get_avatar_storage", return_value=self.storage
        )
        self.avatar_storage_patch.start()
        self.addCleanup(self.avatar_storage_patch.stop)
        self.client.force_login(self.user)

    def test_authenticated_user_gets_deterministic_fallback_in_account_menu(self) -> None:
        first = self.client.get("/profile/avatar/")
        second = self.client.get("/profile/avatar/")
        profile = self.client.get("/profile/")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Content-Type"], "image/svg+xml")
        self.assertEqual(first.content, second.content)
        self.assertEqual(first["Cache-Control"], "private, no-store")
        self.assertContains(profile, 'src="/profile/avatar/"')
        self.assertContains(profile, "Profile picture")

    def test_upload_replaces_avatar_and_remove_returns_to_fallback(self) -> None:
        uploaded = self.client.post("/profile/avatar/upload/", {"avatar": _image_upload((1, 2, 3))})
        self.assertRedirects(uploaded, "/profile/")
        avatar = UserAvatar.objects.get(user=self.user)
        first_key = avatar.object_key
        self.assertTrue(first_key.startswith(f"{self.user.pk}/"))
        self.assertEqual(avatar.content_type, "image/jpeg")
        self.assertEqual(avatar.byte_size, len(self.storage.objects[first_key]))
        self.assertEqual(self.client.get("/profile/avatar/")["Content-Type"], "image/jpeg")

        replaced = self.client.post(
            "/profile/avatar/upload/", {"avatar": _image_upload((7, 8, 9))}
        )
        self.assertRedirects(replaced, "/profile/")
        avatar.refresh_from_db()
        self.assertNotEqual(avatar.object_key, first_key)
        self.assertNotIn(first_key, self.storage.objects)

        removed = self.client.post("/profile/avatar/remove/")
        self.assertRedirects(removed, "/profile/")
        self.assertFalse(UserAvatar.objects.filter(user=self.user).exists())
        self.assertEqual(self.client.get("/profile/avatar/")["Content-Type"], "image/svg+xml")


    def test_invalid_upload_is_rejected_without_creating_avatar(self) -> None:
        invalid = SimpleUploadedFile("avatar.svg", b"<svg/>", content_type="image/svg+xml")

        response = self.client.post("/profile/avatar/upload/", {"avatar": invalid}, follow=True)

        self.assertContains(response, "Choose a JPEG, PNG, or WebP image.")
        self.assertFalse(UserAvatar.objects.filter(user=self.user).exists())
        self.assertFalse(self.storage.objects)

    def test_cross_user_removal_cannot_mutate_another_users_avatar(self) -> None:
        other_user = User.objects.create_user("other-user", password="test-password")
        foreign_key = f"{other_user.pk}/avatar.jpg"
        UserAvatar.objects.create(
            user=other_user,
            object_key=foreign_key,
            content_type="image/jpeg",
            byte_size=10,
        )
        self.storage.objects[foreign_key] = b"other user's image"

        response = self.client.post("/profile/avatar/remove/")

        self.assertRedirects(response, "/profile/")
        self.assertTrue(UserAvatar.objects.filter(user=other_user, object_key=foreign_key).exists())
        self.assertIn(foreign_key, self.storage.objects)

    def test_avatar_table_is_protected_from_supabase_api_roles(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relrowsecurity FROM pg_class "
                "WHERE oid = 'public.qbet_user_avatars'::regclass"
            )
            self.assertTrue(cursor.fetchone()[0])
            for role in ("anon", "authenticated"):
                cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = %s)", [role])
                if cursor.fetchone()[0]:
                    cursor.execute(
                        "SELECT has_table_privilege(%s, %s, 'SELECT')",
                        [role, "public.qbet_user_avatars"],
                    )
                    self.assertFalse(cursor.fetchone()[0], f"{role} can read avatar metadata")

    def test_removal_deletes_metadata_before_private_object_cleanup(self) -> None:
        object_key = f"{self.user.pk}/avatar.jpg"
        UserAvatar.objects.create(
            user=self.user,
            object_key=object_key,
            content_type="image/jpeg",
            byte_size=10,
        )
        self.storage.objects[object_key] = b"stored image"
        original_delete = self.storage.delete

        def assert_reference_removed(key: str) -> None:
            self.assertFalse(UserAvatar.objects.filter(user=self.user).exists())
            original_delete(key)

        with patch.object(self.storage, "delete", side_effect=assert_reference_removed):
            response = self.client.post("/profile/avatar/remove/")

        self.assertRedirects(response, "/profile/")
        self.assertNotIn(object_key, self.storage.objects)

    def test_user_deletion_cleans_private_avatar_after_commit(self) -> None:
        object_key = f"{self.user.pk}/avatar.jpg"
        UserAvatar.objects.create(
            user=self.user,
            object_key=object_key,
            content_type="image/jpeg",
            byte_size=10,
        )
        self.storage.objects[object_key] = b"stored image"

        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

        self.assertFalse(UserAvatar.objects.filter(object_key=object_key).exists())
        self.assertNotIn(object_key, self.storage.objects)

    def test_missing_stored_object_returns_generated_fallback(self) -> None:
        UserAvatar.objects.create(
            user=self.user,
            object_key=f"{self.user.pk}/missing.jpg",
            content_type="image/jpeg",
            byte_size=10,
        )

        response = self.client.get("/profile/avatar/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")

    def test_corrupt_stored_object_returns_generated_fallback(self) -> None:
        object_key = f"{self.user.pk}/corrupt.jpg"
        UserAvatar.objects.create(
            user=self.user,
            object_key=object_key,
            content_type="image/jpeg",
            byte_size=10,
        )
        self.storage.objects[object_key] = b"not a jpeg"

        response = self.client.get("/profile/avatar/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")

    def test_avatar_routes_require_authentication_and_post(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get("/profile/avatar/").status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/profile/avatar/upload/").status_code, 405)


class BlockingDeleteAvatarStorage(MemoryAvatarStorage):
    def __init__(self, blocked_key: str) -> None:
        super().__init__()
        self.blocked_key = blocked_key
        self.delete_started = Event()
        self.allow_delete = Event()
        self._blocked_once = False

    def delete(self, object_key: str) -> None:
        if object_key == self.blocked_key and not self._blocked_once:
            self._blocked_once = True
            self.delete_started.set()
            if not self.allow_delete.wait(timeout=15):
                raise TimeoutError("test did not release blocked avatar deletion")
        super().delete(object_key)


class ProfileAvatarConcurrencyTests(TransactionTestCase):
    def test_remove_racing_with_replacement_preserves_the_replacement(self) -> None:
        user = get_user_model().objects.create_user("avatar-race", password="test-password")
        old_key = f"{user.pk}/old.jpg"
        UserAvatar.objects.create(
            user=user,
            object_key=old_key,
            content_type="image/jpeg",
            byte_size=10,
        )
        storage = BlockingDeleteAvatarStorage(old_key)
        storage.objects[old_key] = b"old image"
        storage_patch = patch("qbet.web.views.get_avatar_storage", return_value=storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

        remove_errors: list[BaseException] = []

        def remove_avatar() -> None:
            close_old_connections()
            try:
                client = Client()
                client.force_login(user)
                response = client.post("/profile/avatar/remove/")
                if response.status_code != 302:
                    raise AssertionError(f"remove returned HTTP {response.status_code}")
            except BaseException as error:
                remove_errors.append(error)
            finally:
                close_old_connections()

        worker = Thread(target=remove_avatar)
        worker.start()
        self.assertTrue(storage.delete_started.wait(timeout=10))

        upload_client = Client()
        upload_client.force_login(user)
        upload_response = upload_client.post(
            "/profile/avatar/upload/", {"avatar": _image_upload((90, 120, 150))}
        )
        self.assertEqual(upload_response.status_code, 302)
        replacement = UserAvatar.objects.get(user=user)
        self.assertNotEqual(replacement.object_key, old_key)
        self.assertIn(replacement.object_key, storage.objects)

        storage.allow_delete.set()
        worker.join(timeout=15)

        self.assertFalse(worker.is_alive())
        self.assertEqual(remove_errors, [])
        self.assertEqual(UserAvatar.objects.get(user=user).object_key, replacement.object_key)
        self.assertIn(replacement.object_key, storage.objects)
