"""User-owned Bonus Offer intake and listing for the authenticated web shell."""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from qbet.providers import GERMAN_JURISDICTION, ProviderStatus
from qbet.storage.models import SportsbookProviderRow
from qbet.web.models import BonusOffer

_FORM_DATA_KEY = "qbet.bonus_offer.form_data"
_DIALOG_OPEN_KEY = "qbet.bonus_offer.dialog_open"


class SportsbookProviderChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: SportsbookProviderRow) -> str:
        return obj.display_name


class BonusOfferForm(forms.ModelForm):
    provider = SportsbookProviderChoiceField(
        queryset=SportsbookProviderRow.objects.none(),
        to_field_name="provider_id",
        empty_label="Choose sportsbook",
    )

    class Meta:
        model = BonusOffer
        fields = (
            "provider",
            "name",
            "promotion_type",
            "promotion_value",
            "currency",
            "required_stake",
            "minimum_odds",
            "wagering_requirement",
            "stake_return_rule",
            "valid_until",
            "notes",
        )
        widgets = {
            "valid_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "name": "Offer name",
            "promotion_value": "Promotion value",
            "required_stake": "Required stake",
            "minimum_odds": "Minimum qualifying odds",
            "wagering_requirement": "Wagering / turnover requirement",
            "stake_return_rule": "Free-bet stake rule",
            "valid_until": "Valid until",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        provider_field = cast(SportsbookProviderChoiceField, self.fields["provider"])
        provider_field.queryset = SportsbookProviderRow.objects.filter(
            jurisdiction=GERMAN_JURISDICTION,
            sports_betting=True,
            online=True,
            status=ProviderStatus.ACTIVE.value,
        ).order_by("display_name", "provider_id")
        self.fields["stake_return_rule"].required = False
        self.fields["promotion_value"].required = False
        self.fields["required_stake"].required = False
        self.fields["minimum_odds"].required = False
        self.fields["wagering_requirement"].required = False

    def clean_valid_until(self):
        value = self.cleaned_data["valid_until"]
        if value <= timezone.now():
            raise forms.ValidationError("Choose a future expiry time.")
        return value

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        promotion_type = cleaned.get("promotion_type")
        promotion_value = cleaned.get("promotion_value")
        required_stake = cleaned.get("required_stake")
        stake_return_rule = cleaned.get("stake_return_rule")

        if promotion_type == BonusOffer.PromotionType.QUALIFYING_BET:
            if required_stake is None:
                self.add_error("required_stake", "Qualifying bets require a stake amount.")
            if promotion_value is not None:
                self.add_error(
                    "promotion_value",
                    "Qualifying-bet intake does not use a bonus amount.",
                )
            if stake_return_rule:
                self.add_error(
                    "stake_return_rule",
                    "Qualifying-bet intake does not use a free-bet stake rule.",
                )
        elif promotion_type == BonusOffer.PromotionType.FREE_BET:
            if promotion_value is None:
                self.add_error("promotion_value", "Free bets require a promotional amount.")
            if not stake_return_rule:
                self.add_error(
                    "stake_return_rule",
                    "Choose whether the promotional stake is returned.",
                )
            if required_stake is not None:
                self.add_error(
                    "required_stake",
                    "Free-bet intake does not use a qualifying stake amount.",
                )
        return cleaned


def bonus_offer_shell_context(request: HttpRequest) -> dict[str, object]:
    """Expose one reusable Add-dialog form without duplicating provider choices."""

    if not request.user.is_authenticated:
        return {}
    raw = request.session.pop(_FORM_DATA_KEY, None)
    open_dialog = bool(request.session.pop(_DIALOG_OPEN_KEY, False))
    data = raw if isinstance(raw, dict) else None
    return {
        "bonus_offer_form": BonusOfferForm(data=data),
        "bonus_offer_dialog_open": open_dialog,
    }


@login_required
@require_GET
def bonus_offer_list(request: HttpRequest) -> HttpResponse:
    offers = tuple(
        BonusOffer.objects.filter(user=request.user)
        .select_related("provider")
        .order_by("valid_until", "-updated_at", "id")
    )
    return render(
        request,
        "qbet_web/bonus_offer_list.html",
        {"bonus_offers": offers},
    )


@login_required
@require_POST
def bonus_offer_create(request: HttpRequest) -> HttpResponse:
    form = BonusOfferForm(request.POST)
    return_to = _safe_return_path(request)
    if not form.is_valid():
        request.session[_FORM_DATA_KEY] = {
            name: request.POST.get(name, "")
            for name in form.fields
        }
        request.session[_DIALOG_OPEN_KEY] = True
        messages.error(request, "Bonus Offer was not saved. Check the highlighted fields.")
        return redirect(return_to)

    offer = form.save(commit=False)
    offer.user = cast(User, request.user)
    offer.save()
    messages.success(request, "Bonus Offer saved.")
    return redirect(return_to)


def _safe_return_path(request: HttpRequest) -> str:
    candidate = str(request.POST.get("return_to") or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return "/bonus-offers/"
