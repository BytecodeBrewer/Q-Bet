from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from qbet.web.admin_guard import (
    has_other_active_admin,
    is_active_admin,
    last_superuser_message,
)

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=User, dispatch_uid="qbet.protect_last_active_superuser.save")
def protect_last_active_superuser_on_save(sender, instance: User, **kwargs) -> None:  # noqa: ARG001
    if instance.pk is None:
        return
    try:
        previous = User.objects.get(pk=instance.pk)
    except User.DoesNotExist:
        return
    if is_active_admin(previous) and not is_active_admin(instance):
        if not has_other_active_admin(exclude_pk=instance.pk):
            raise ValidationError(last_superuser_message())


@receiver(pre_delete, sender=User, dispatch_uid="qbet.delete_user_avatar_storage_object")
def schedule_user_avatar_storage_cleanup(sender, instance: User, using, **kwargs) -> None:  # noqa: ARG001
    from qbet.web.avatars import AvatarStorageError, get_avatar_storage
    from qbet.web.models import UserAvatar

    object_key = (
        UserAvatar.objects.using(using)
        .filter(user_id=instance.pk)
        .values_list("object_key", flat=True)
        .first()
    )
    if object_key is None:
        return

    def delete_object() -> None:
        try:
            get_avatar_storage().delete(object_key)
        except AvatarStorageError:
            logger.exception("Could not remove private avatar object after user deletion.")

    transaction.on_commit(delete_object, using=using, robust=True)
