"""User-owned sportsbook account registry and product-safe web boundary."""

from __future__ import annotations

from datetime import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from qbet.provider_accounts import (
    ProviderAccountSnapshot,
    ProviderAccountSource,
    ProviderAccountStatus,
)
from qbet.providers import GERMAN_JURISDICTION, ProviderStatus
from qbet.storage.models import SportsbookProviderRow
from qbet.web.models import ProviderAccount


PROVIDER_ACCOUNT_STATUS_CHOICES = (
    (ProviderAccountStatus.NOT_CONFIGURED.value, "Not configured"),
    (ProviderAccountStatus.DECLARED.value, "Declared / configured"),
    (ProviderAccountStatus.ACTIVE.value, "Active"),
    (ProviderAccountStatus.NEEDS_VERIFICATION.value, "Needs verification"),
    (ProviderAccountStatus.UNAVAILABLE.value, "Unavailable"),
    (ProviderAccountStatus.FROZEN.value, "Frozen / restricted"),
)


class ProviderAccountForm(forms.Form):
    status = forms.ChoiceField(choices=PROVIDER_ACCOUNT_STATUS_CHOICES)
    nickname = forms.CharField(required=False, max_length=80)
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        allowed = {*self.fields, "csrfmiddlewaretoken"}
        if set(self.data) - allowed:
            raise forms.ValidationError("Unsupported provider-account field.")
        return cleaned


class PostgresProviderAccountRegistry:
    """Owner-scoped provider-account persistence behind the typed read contract."""

    def list_for_user(self, *, user_id: int) -> tuple[ProviderAccountSnapshot, ...]:
        accounts = {
            item.provider.provider_id: item
            for item in ProviderAccount.objects.filter(user_id=user_id).select_related("provider")
        }
        providers = {
            provider.provider_id: provider
            for provider in SportsbookProviderRow.objects.filter(
                jurisdiction=GERMAN_JURISDICTION,
                sports_betting=True,
            ).order_by("display_name", "provider_id")
        }
        for account in accounts.values():
            providers.setdefault(account.provider.provider_id, account.provider)

        return tuple(
            self._snapshot(provider, accounts.get(provider.provider_id))
            for provider in sorted(
                providers.values(),
                key=lambda item: (item.display_name.casefold(), item.provider_id),
            )
        )

    def get_for_user(self, *, user_id: int, provider_id: str) -> ProviderAccountSnapshot:
        provider = self._provider(provider_id)
        account = (
            ProviderAccount.objects.filter(user_id=user_id, provider=provider)
            .select_related("provider")
            .first()
        )
        return self._snapshot(provider, account)

    def set_manual(
        self,
        *,
        user_id: int,
        provider_id: str,
        status: ProviderAccountStatus,
        nickname: str = "",
        notes: str = "",
    ) -> ProviderAccountSnapshot:
        provider = self._provider(provider_id)
        if status is ProviderAccountStatus.NOT_CONFIGURED:
            ProviderAccount.objects.filter(user_id=user_id, provider=provider).delete()
            return self._snapshot(provider, None)

        account, _ = ProviderAccount.objects.update_or_create(
            user_id=user_id,
            provider=provider,
            defaults={
                "status": status.value,
                "source": ProviderAccountSource.MANUAL.value,
                "nickname": nickname.strip(),
                "notes": notes.strip(),
                "last_verified_at": None,
            },
        )
        account = ProviderAccount.objects.select_related("provider").get(pk=account.pk)
        return self._snapshot(provider, account)

    def record_verified(
        self,
        *,
        user_id: int,
        provider_id: str,
        status: ProviderAccountStatus,
        source: ProviderAccountSource,
        verified_at: datetime | None = None,
    ) -> ProviderAccountSnapshot:
        """Future adapter seam; the normal-user GUI never calls this method."""

        if source is ProviderAccountSource.MANUAL:
            raise ValueError("verified provider state requires an external source")
        if status is ProviderAccountStatus.NOT_CONFIGURED:
            raise ValueError("verified provider state cannot be not configured")

        provider = self._provider(provider_id)
        account, _ = ProviderAccount.objects.update_or_create(
            user_id=user_id,
            provider=provider,
            defaults={
                "status": status.value,
                "source": source.value,
                "last_verified_at": verified_at or timezone.now(),
            },
        )
        account = ProviderAccount.objects.select_related("provider").get(pk=account.pk)
        return self._snapshot(provider, account)

    @staticmethod
    def _provider(provider_id: str) -> SportsbookProviderRow:
        try:
            provider = SportsbookProviderRow.objects.get(provider_id=provider_id)
        except SportsbookProviderRow.DoesNotExist as error:
            raise LookupError("unknown sportsbook provider") from error
        if provider.jurisdiction != GERMAN_JURISDICTION or not provider.sports_betting:
            raise LookupError("provider is outside the supported sportsbook catalog")
        return provider

    @staticmethod
    def _snapshot(
        provider: SportsbookProviderRow,
        account: ProviderAccount | None,
    ) -> ProviderAccountSnapshot:
        eligible = (
            provider.status == ProviderStatus.ACTIVE.value
            and provider.jurisdiction == GERMAN_JURISDICTION
            and provider.sports_betting
            and provider.online
        )
        if account is None:
            return ProviderAccountSnapshot(
                provider_id=provider.provider_id,
                provider_name=provider.display_name,
                status=ProviderAccountStatus.NOT_CONFIGURED,
                provider_eligible=eligible,
            )
        return ProviderAccountSnapshot(
            provider_id=provider.provider_id,
            provider_name=provider.display_name,
            status=ProviderAccountStatus(account.status),
            source=ProviderAccountSource(account.source),
            nickname=account.nickname,
            notes=account.notes,
            configured_at=account.configured_at,
            last_verified_at=account.last_verified_at,
            provider_eligible=eligible,
        )


REGISTRY = PostgresProviderAccountRegistry()


@login_required
@require_GET
def provider_account_list(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not isinstance(user, User) or user.pk is None:
        raise Http404("User account is unavailable.")
    return render(
        request,
        "qbet_web/provider_accounts.html",
        {
            "accounts": REGISTRY.list_for_user(user_id=user.pk),
            "status_choices": PROVIDER_ACCOUNT_STATUS_CHOICES,
        },
    )


@login_required
@require_POST
def provider_account_update(request: HttpRequest, provider_id: str) -> HttpResponse:
    user = request.user
    if not isinstance(user, User) or user.pk is None:
        raise Http404("User account is unavailable.")

    form = ProviderAccountForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Provider account details were not accepted.")
        return redirect("provider-account-list")

    try:
        status = ProviderAccountStatus(str(form.cleaned_data["status"]))
        snapshot = REGISTRY.set_manual(
            user_id=user.pk,
            provider_id=provider_id,
            status=status,
            nickname=str(form.cleaned_data.get("nickname") or ""),
            notes=str(form.cleaned_data.get("notes") or ""),
        )
    except (LookupError, ValueError) as error:
        raise Http404("Provider account is unavailable.") from error

    if snapshot.status is ProviderAccountStatus.NOT_CONFIGURED:
        messages.success(request, f"{snapshot.provider_name} account configuration removed.")
    else:
        messages.success(
            request,
            f"{snapshot.provider_name} account status saved as manual / unverified.",
        )
    return redirect("provider-account-list")
