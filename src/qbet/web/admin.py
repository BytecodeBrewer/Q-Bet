from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from qbet.notifications import notification_recipient_status
from qbet.web.admin_guard import (
    has_other_active_admin,
    is_active_admin,
    last_superuser_message,
)
from qbet.web.forms import NotificationReadyUserCreationForm
from qbet.web.models import CustomerReportAccess


class ProtectedUserChangeForm(UserChangeForm):
    """Protect admin invariants while allowing legacy incomplete profiles to remain visible."""

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

        original_status = notification_recipient_status(
            user_id=original.get_username(),
            first_name=original.first_name,
            last_name=original.last_name,
            email=original.email,
        )
        original_complete = bool(
            original_status.ready and original.first_name.strip() and original.last_name.strip()
        )
        if original_complete:
            first_name = str(cleaned_data.get("first_name", original.first_name) or "").strip()
            last_name = str(cleaned_data.get("last_name", original.last_name) or "").strip()
            resulting_status = notification_recipient_status(
                user_id=str(cleaned_data.get("username", original.get_username()) or ""),
                first_name=first_name,
                last_name=last_name,
                email=str(cleaned_data.get("email", original.email) or ""),
            )
            if not first_name or not last_name or not resulting_status.ready:
                raise forms.ValidationError(
                    "Notification-ready users must keep first name, last name, and a valid email."
                )
        return cleaned_data


class ProtectedUserAdmin(UserAdmin):
    form = ProtectedUserChangeForm
    add_form = NotificationReadyUserCreationForm
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "email",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

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


@admin.register(CustomerReportAccess)
class CustomerReportAccessAdmin(admin.ModelAdmin):
    list_display = ("report_id", "user", "granted_at")
    search_fields = ("report_id", "user__username")
    raw_id_fields = ("user",)
