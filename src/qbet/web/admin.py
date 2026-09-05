from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from qbet.web.admin_guard import (
    has_other_active_admin,
    is_active_admin,
    last_superuser_message,
)


class ProtectedUserChangeForm(UserChangeForm):
    """Give administrators a friendly validation error before the DB guard fires."""

    def clean(self):
        cleaned_data = super().clean() or {}
        if not self.instance.pk:
            return cleaned_data

        try:
            original = User.objects.get(pk=self.instance.pk)
        except User.DoesNotExist:
            return cleaned_data

        resulting_active_admin = bool(
            cleaned_data.get("is_active", original.is_active)
            and cleaned_data.get("is_staff", original.is_staff)
            and cleaned_data.get("is_superuser", original.is_superuser)
        )
        if is_active_admin(original) and not resulting_active_admin:
            if not has_other_active_admin(exclude_pk=original.pk):
                raise forms.ValidationError(last_superuser_message())
        return cleaned_data


class ProtectedUserAdmin(UserAdmin):
    form = ProtectedUserChangeForm

    def delete_model(self, request, obj: User) -> None:
        if is_active_admin(obj) and not has_other_active_admin(exclude_pk=obj.pk):
            raise PermissionDenied(last_superuser_message())
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset) -> None:
        active_admin_ids = set(
            User.objects.filter(is_active=True, is_staff=True, is_superuser=True).values_list(
                "pk", flat=True
            )
        )
        deleting_ids = set(queryset.values_list("pk", flat=True))
        if active_admin_ids and not (active_admin_ids - deleting_ids):
            raise PermissionDenied(last_superuser_message())
        super().delete_queryset(request, queryset)


if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, ProtectedUserAdmin)
