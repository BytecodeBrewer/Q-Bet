"""Controlled manual correction of where authoritative portfolio capital is located."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow, SportsbookProviderRow
from qbet.web.models import PortfolioCapitalLocation, PortfolioLedgerAccess


class ProviderCapitalLocationForm(forms.Form):
    provider = forms.ModelChoiceField(
        queryset=SportsbookProviderRow.objects.none(),
        to_field_name="provider_id",
        empty_label="Choose provider",
    )
    currency = forms.ChoiceField(choices=(("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD")))
    amount = forms.DecimalField(
        min_value=Decimal("0"), max_digits=24, decimal_places=8, label="Capital at provider"
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        provider_field = cast(forms.ModelChoiceField, self.fields["provider"])
        provider_field.queryset = SportsbookProviderRow.objects.filter(
            sports_betting=True, online=True
        ).order_by("display_name", "provider_id")


@login_required
@require_POST
def portfolio_location_update(request: HttpRequest) -> HttpResponse:
    """Correct provider allocation without changing authoritative total capital."""

    form = ProviderCapitalLocationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Capital location was not changed. Check the entered values.")
        return redirect("portfolio")

    user = cast(User, request.user)
    provider = cast(SportsbookProviderRow, form.cleaned_data["provider"])
    currency = cast(str, form.cleaned_data["currency"])
    mode = "execution"

    if not user.is_staff and not PortfolioLedgerAccess.objects.filter(
        user=user, mode=mode, currency=currency
    ).exists():
        messages.error(request, "This capital context is not available for your account.")
        return redirect("portfolio")

    amount = cast(Decimal, form.cleaned_data["amount"])

    with transaction.atomic():
        try:
            row = PortfolioLedgerRow.objects.select_for_update().get(
                mode=mode, currency=currency
            )
            ledger = PortfolioLedger.model_validate(row.payload)
        except (PortfolioLedgerRow.DoesNotExist, ValueError):
            messages.error(request, "No authoritative Execution ledger exists for that currency.")
            return redirect("portfolio")

        tracked_total = (
            ledger.balance.available
            + ledger.balance.reserved
            + ledger.balance.locked
            + ledger.balance.pending
        )
        allocated_elsewhere = sum(
            (
                item.amount
                for item in PortfolioCapitalLocation.objects.select_for_update()
                .filter(user=user, mode=mode, currency=currency)
                .exclude(provider=provider)
            ),
            Decimal(0),
        )
        if allocated_elsewhere + amount > tracked_total:
            messages.error(
                request,
                "Provider locations cannot exceed authoritative tracked capital.",
            )
            return redirect("portfolio")

        PortfolioCapitalLocation.objects.update_or_create(
            user=user,
            provider=provider,
            mode=mode,
            currency=currency,
            defaults={"amount": amount, "note": cast(str, form.cleaned_data["note"])},
        )

    messages.success(
        request,
        f"{provider.display_name} capital location updated. Total capital was not changed.",
    )
    return redirect("portfolio")
