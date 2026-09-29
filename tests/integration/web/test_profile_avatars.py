from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
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
