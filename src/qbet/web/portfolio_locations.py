"""Portfolio balance editing and targeted read-only central-account refresh."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from qbet.bank import (
    BankBalanceRequest,
    BankBalanceStatus,
    BunqBalanceProvider,
    BunqConfigurationError,
    BunqOperatingMode,
    BunqSdkTransport,
    BunqSettings,
    ReadOnlyBankBalanceService,
)
from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.domain.models import Currency
from qbet.ledger import PortfolioLedger
from qbet.providers import GERMAN_JURISDICTION, ProviderStatus
from qbet.storage.models import PortfolioLedgerRow, SportsbookProviderRow
from qbet.web.models import PortfolioCapitalLocation, PortfolioLedgerAccess


@dataclass(frozen=True)
class CentralAccountBalance:
    """Fresh read-only bank observation returned to the Portfolio UI."""

    amount: Decimal
    currency: Currency
    observed_at: datetime


class ProviderCapitalLocationForm(forms.Form):
    provider = forms.ModelChoiceField(
        queryset=SportsbookProviderRow.objects.none(),
        to_field_name="provider_id",
        widget=forms.HiddenInput(),
    )
    currency = forms.ChoiceField(
        choices=(("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD")),
        widget=forms.HiddenInput(),
    )
    amount = forms.DecimalField(
        min_value=Decimal("0"), max_digits=24, decimal_places=8, label="Balance"
    )
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        provider_field = cast(forms.ModelChoiceField, self.fields["provider"])
        provider_field.queryset = SportsbookProviderRow.objects.filter(
            jurisdiction=GERMAN_JURISDICTION,
            sports_betting=True,
            online=True,
            status=ProviderStatus.ACTIVE.value,
        ).order_by("display_name", "provider_id")


def read_bunq_balance(currency: Currency) -> CentralAccountBalance:
    """Read a fresh bunq balance without mutating PortfolioLedger."""

    settings = BunqSettings.from_environment()
    source = DataSourceMetadata(
        provider_id="bunq",
        source_id=(
            "bunq_sandbox"
            if settings.mode is BunqOperatingMode.SANDBOX
            else "bunq_read_only"
        ),
        transport=SourceTransport.API,
    )
    started_at = datetime.now(UTC)
    correlation_id = uuid4()
    reader = ReadOnlyBankBalanceService(
        BunqBalanceProvider(
            source=source,
            account_reference=settings.account_reference,
            transport=BunqSdkTransport(settings),
        )
    )
    outcome = reader.read_balance(
        BankBalanceRequest(
            source=source,
            account_reference=settings.account_reference,
            currency=currency,
            correlation_id=correlation_id,
            fresh_after=started_at - timedelta(minutes=5),
        )
    )
    if outcome.status is not BankBalanceStatus.AVAILABLE or outcome.balance is None:
        raise ValueError(outcome.reason_code or "bunq_balance_unavailable")
    balance = outcome.balance
    amount = (
        balance.current_balance
        if balance.current_balance is not None
        else balance.available_balance
    )
    return CentralAccountBalance(
        amount=amount,
        currency=balance.currency,
        observed_at=balance.observed_at,
    )


@login_required
@require_POST
def portfolio_central_refresh(request: HttpRequest) -> JsonResponse:
    """Refresh only the central bunq card through the read-only bank boundary."""

    user = cast(User, request.user)
    raw_currency = request.POST.get("currency", "").upper()
    if raw_currency not in {"EUR", "GBP", "USD"}:
        return JsonResponse(
            {"status": "invalid", "message": "Unsupported currency."},
            status=400,
        )
    currency = cast(Currency, raw_currency)
    if not user.is_staff and not PortfolioLedgerAccess.objects.filter(
        user=user, mode="execution", currency=currency
    ).exists():
        return JsonResponse(
            {"status": "forbidden", "message": "This capital context is not available."},
            status=403,
        )
    if not PortfolioLedgerRow.objects.filter(
        mode="execution", currency=currency
    ).exists():
        return JsonResponse(
            {"status": "unavailable", "message": "No Execution balance exists for this currency."},
            status=404,
        )

    try:
        balance = read_bunq_balance(currency)
    except BunqConfigurationError:
        return JsonResponse(
            {"status": "unavailable", "message": "bunq balance refresh is not configured."},
            status=503,
        )
    except ValueError:
        return JsonResponse(
            {"status": "unavailable", "message": "bunq balance could not be refreshed."},
            status=503,
        )

    return JsonResponse(
        {
            "status": "ok",
            "amount": format(balance.amount, "f"),
            "currency": balance.currency,
            "observed_at": balance.observed_at.isoformat(),
        }
    )


@login_required
@require_POST
def portfolio_location_update(request: HttpRequest) -> HttpResponse:
    """Record one provider balance without changing authoritative total capital."""

    form = ProviderCapitalLocationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Provider balance was not changed. Check the entered values.")
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
            messages.error(request, "No Execution balance exists for that currency.")
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
                "Provider balances cannot exceed total portfolio capital.",
            )
            return redirect("portfolio")

        PortfolioCapitalLocation.objects.update_or_create(
            user=user,
            provider=provider,
            mode=mode,
            currency=currency,
            defaults={"amount": amount, "note": cast(str, form.cleaned_data["note"])},
        )

    messages.success(request, f"{provider.display_name} balance updated.")
    return redirect("portfolio")
