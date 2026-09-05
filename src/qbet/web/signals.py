from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from qbet.web.admin_guard import (
    has_other_active_admin,
    is_active_admin,
    last_superuser_message,
)


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


@receiver(pre_delete, sender=User, dispatch_uid="qbet.protect_last_active_superuser.delete")
def protect_last_active_superuser_on_delete(sender, instance: User, **kwargs) -> None:  # noqa: ARG001
    if is_active_admin(instance) and not has_other_active_admin(exclude_pk=instance.pk):
        raise ValidationError(last_superuser_message())
