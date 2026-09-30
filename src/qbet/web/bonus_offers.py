"""User-owned Bonus Offer intake, duplicate safety, editing and history."""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qbet.providers import GERMAN_JURISDICTION, ProviderStatus
from qbet.storage.models import SportsbookProviderRow
from qbet.web.bonus_offer_policy import (
    BonusOfferCandidate,
    bonus_offer_guidance,
    bonus_offer_snapshot,
    evaluate_bonus_coverage,
    find_bonus_offer_duplicate,
)
from qbet.web.models import BonusOffer, BonusOfferRevision
from qbet.workflow.bonus_dependencies import BonusOfferWorkInvalidator

_FORM_DATA_KEY = "qbet.bonus_offer.form_data"
_DIALOG_OPEN_KEY = "qbet.bonus_offer.dialog_open"
_DUPLICATE_OFFER_KEY = "qbet.bonus_offer.duplicate_offer"
_DUPLICATE_KIND_KEY = "qbet.bonus_offer.duplicate_kind"


class SportsbookProviderChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: SportsbookProviderRow) -> str:
        return obj.display_name


class BonusOfferForm(forms.ModelForm):
    """User-facing promotion terms; internal strategy is derived, never selected."""

    provider = SportsbookProviderChoiceField(
        queryset=SportsbookProviderRow.objects.none(),
        to_field_name="provider_id",
        empty_label="Choose sportsbook",
    )
    confirm_near_duplicate = forms.BooleanField(
        required=False,
        label="I reviewed the similar offer and want to save this one anyway.",
    )

    class Meta:
        model = BonusOffer
        fields = (
            "provider",
            "name",
            "promotion_shape",
            "promotion_value",
            "currency",
            "required_stake",
            "minimum_odds",
            "wagering_requirement",
            "stake_return_rule",
            "valid_until",
            "unsupported_terms",
        )
        widgets = {
            "valid_until": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "unsupported_terms": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "name": "Promotion title",
            "promotion_shape": "What does the sportsbook offer?",
            "promotion_value": "Free-bet / bonus value",
            "required_stake": "Required qualifying stake",
            "minimum_odds": "Minimum eligible odds",
            "wagering_requirement": "Turnover requirement",
            "stake_return_rule": "Is the free-bet stake returned?",
            "valid_until": "Promotion deadline",
            "unsupported_terms": "Other material conditions",
        }
        help_texts = {
            "promotion_shape": (
                "Describe the promotion terms. Q-Bet derives the supported calculation stage."
            ),
            "promotion_value": "The advertised reward or free-bet amount.",
            "required_stake": "Amount that must be wagered to earn the advertised reward.",
            "minimum_odds": "Leave empty if the promotion has no minimum-odds condition.",
            "wagering_requirement": (
                "Record the advertised turnover multiplier. Non-zero turnover is saved "
                "as Needs review because current BonusEngine math does not model it."
            ),
            "unsupported_terms": (
                "Record any other material condition. Q-Bet saves the offer as Needs review "
                "instead of silently ignoring it."
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        provider_field = cast(SportsbookProviderChoiceField, self.fields["provider"])
        eligible_provider = Q(
            jurisdiction=GERMAN_JURISDICTION,
            sports_betting=True,
            online=True,
            status=ProviderStatus.ACTIVE.value,
        )
        if self.instance.pk and self.instance.provider_id:
            eligible_provider |= Q(provider_id=self.instance.provider_id)
        provider_field.queryset = SportsbookProviderRow.objects.filter(
            eligible_provider
        ).order_by("display_name", "provider_id")

        shape_choices = [
            (BonusOffer.PromotionShape.BET_AND_GET, "Bet & get a free bet"),
            (BonusOffer.PromotionShape.FREE_BET, "Free bet already available"),
            (BonusOffer.PromotionShape.OTHER, "Other / unsupported promotion"),
        ]
        if (
            self.instance.pk
            and self.instance.effective_promotion_shape
            == BonusOffer.PromotionShape.LEGACY_QUALIFYING_STAGE
        ):
            shape_choices.append(
                (
                    BonusOffer.PromotionShape.LEGACY_QUALIFYING_STAGE,
                    "Qualifying wager stage (legacy)",
                )
            )
        shape_field = cast(forms.ChoiceField, self.fields["promotion_shape"])
        shape_field.choices = shape_choices
        shape_field.required = True

        for name in (
            "promotion_value",
            "required_stake",
            "minimum_odds",
            "wagering_requirement",
            "stake_return_rule",
            "unsupported_terms",
        ):
            self.fields[name].required = False

    def clean_valid_until(self):
        value = self.cleaned_data["valid_until"]
        if value <= timezone.now():
            raise forms.ValidationError("Choose a future expiry time.")
        return value

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        shape = cleaned.get("promotion_shape")
        promotion_value = cleaned.get("promotion_value")
        required_stake = cleaned.get("required_stake")
        stake_return_rule = cleaned.get("stake_return_rule")
        unsupported_terms = str(cleaned.get("unsupported_terms") or "").strip()

        if shape == BonusOffer.PromotionShape.BET_AND_GET:
            if required_stake is None:
                self.add_error(
                    "required_stake",
                    "Bet & get promotions require the qualifying stake.",
                )
            if promotion_value is None:
                self.add_error(
                    "promotion_value",
                    "Record the advertised free-bet / bonus value.",
                )
            if stake_return_rule:
                self.add_error(
                    "stake_return_rule",
                    "The qualifying stage does not use the later free-bet stake rule.",
                )
        elif shape == BonusOffer.PromotionShape.LEGACY_QUALIFYING_STAGE:
            if required_stake is None:
                self.add_error("required_stake", "A qualifying stake is required.")
        elif shape == BonusOffer.PromotionShape.FREE_BET:
            if promotion_value is None:
                self.add_error("promotion_value", "Record the available free-bet value.")
            if not stake_return_rule:
                self.add_error(
                    "stake_return_rule",
                    "Choose whether the promotional stake is returned.",
                )
            if required_stake is not None:
                self.add_error(
                    "required_stake",
                    "An already available free bet does not use a qualifying stake.",
                )
        elif shape == BonusOffer.PromotionShape.OTHER:
            if not unsupported_terms:
                self.add_error(
                    "unsupported_terms",
                    "Describe the material condition that current BonusEngine cannot model.",
                )
        return cleaned

    def save(self, commit: bool = True) -> BonusOffer:
        offer = cast(BonusOffer, super().save(commit=False))
        shape = str(self.cleaned_data["promotion_shape"])
        if shape in {
            BonusOffer.PromotionShape.BET_AND_GET,
            BonusOffer.PromotionShape.LEGACY_QUALIFYING_STAGE,
        }:
            offer.promotion_type = BonusOffer.PromotionType.QUALIFYING_BET
        elif shape == BonusOffer.PromotionShape.FREE_BET:
            offer.promotion_type = BonusOffer.PromotionType.FREE_BET
        else:
            offer.promotion_type = ""
        if commit:
            offer.save()
            self.save_m2m()
        return offer

    def duplicate_candidate(self) -> BonusOfferCandidate:
        if not self.is_valid():
            raise ValueError("duplicate candidate requires a valid BonusOfferForm")
        provider = cast(SportsbookProviderRow, self.cleaned_data["provider"])
        return BonusOfferCandidate(
            provider_id=provider.provider_id,
            promotion_shape=str(self.cleaned_data["promotion_shape"]),
            promotion_value=self.cleaned_data.get("promotion_value"),
            currency=str(self.cleaned_data["currency"]),
            required_stake=self.cleaned_data.get("required_stake"),
            minimum_odds=self.cleaned_data.get("minimum_odds"),
            wagering_requirement=self.cleaned_data.get("wagering_requirement"),
            stake_return_rule=str(self.cleaned_data.get("stake_return_rule") or ""),
            valid_until=self.cleaned_data["valid_until"],
            name=str(self.cleaned_data["name"]),
            unsupported_terms=str(self.cleaned_data.get("unsupported_terms") or ""),
        )


def bonus_offer_shell_context(request: HttpRequest) -> dict[str, object]:
    """Expose one reusable Add-dialog form without duplicating provider choices."""

    if not request.user.is_authenticated:
        return {}
    raw = request.session.pop(_FORM_DATA_KEY, None)
    open_dialog = bool(request.session.pop(_DIALOG_OPEN_KEY, False))
    duplicate_id = request.session.pop(_DUPLICATE_OFFER_KEY, None)
    duplicate_kind = str(request.session.pop(_DUPLICATE_KIND_KEY, "") or "")
    data = raw if isinstance(raw, dict) else None
    duplicate = None
    if duplicate_id is not None:
        duplicate = BonusOffer.objects.filter(
            pk=duplicate_id,
            user=request.user,
        ).select_related("provider").first()
    return {
        "bonus_offer_form": BonusOfferForm(data=data),
        "bonus_offer_dialog_open": open_dialog,
        "bonus_offer_duplicate": duplicate,
        "bonus_offer_duplicate_kind": duplicate_kind,
    }


@login_required
@require_GET
def bonus_offer_list(request: HttpRequest) -> HttpResponse:
    offers = tuple(
        BonusOffer.objects.filter(user=request.user)
        .select_related("provider")
        .prefetch_related("provider__external_identities", "revisions")
        .order_by("valid_until", "-updated_at", "id")
    )
    ready = tuple(
        offer
        for offer in offers
        if not offer.is_retired and not offer.is_expired and offer.is_preparation_ready
    )
    health = evaluate_bonus_coverage(
        usable_offer_count=len(ready),
        provider_count=len({offer.provider.provider_id for offer in ready}),
    )
    return render(
        request,
        "qbet_web/bonus_offer_list.html",
        {
            "bonus_offers": offers,
            "bonus_offer_guidance": bonus_offer_guidance(health),
        },
    )


@login_required
@require_POST
def bonus_offer_create(request: HttpRequest) -> HttpResponse:
    form = BonusOfferForm(request.POST)
    return_to = _safe_return_path(request)
    if not form.is_valid():
        _preserve_dialog(request, form)
        messages.error(request, "Bonus Offer was not saved. Check the highlighted fields.")
        return redirect(return_to)

    duplicate = find_bonus_offer_duplicate(
        user_id=cast(int, request.user.pk),
        candidate=form.duplicate_candidate(),
    )
    if duplicate is not None:
        confirmed = (
            duplicate.kind == "near"
            and form.cleaned_data["confirm_near_duplicate"]
            and request.POST.get("duplicate_offer_id") == str(duplicate.offer.pk)
        )
        if duplicate.kind == "exact" or not confirmed:
            _preserve_dialog(request, form)
            request.session[_DUPLICATE_OFFER_KEY] = duplicate.offer.pk
            request.session[_DUPLICATE_KIND_KEY] = duplicate.kind
            if duplicate.kind == "exact":
                messages.error(request, "This Bonus Offer is an exact duplicate and was not saved.")
            else:
                messages.warning(
                    request,
                    "A similar Bonus Offer already exists. Review it before saving another.",
                )
            return redirect(return_to)

    offer = form.save(commit=False)
    offer.user = cast(User, request.user)
    offer.save()
    messages.success(
        request,
        "Bonus Offer saved as Needs review." if offer.needs_review else "Bonus Offer saved.",
    )
    return redirect(return_to)


@login_required
@require_http_methods(["GET", "POST"])
def bonus_offer_edit(request: HttpRequest, offer_id: int) -> HttpResponse:
    offer = get_object_or_404(
        BonusOffer.objects.select_related("provider"),
        pk=offer_id,
        user=request.user,
    )
    if offer.is_retired:
        messages.error(request, "Removed Bonus Offers are historical and cannot be edited.")
        return redirect("bonus-offer-list")
    if offer.is_expired:
        messages.error(request, "Expired Bonus Offers are historical and cannot be edited.")
        return redirect("bonus-offer-list")

    before_snapshot = bonus_offer_snapshot(offer)
    form = BonusOfferForm(request.POST or None, instance=offer)
    duplicate = None
    duplicate_kind = ""
    if request.method == "POST" and form.is_valid():
        duplicate = find_bonus_offer_duplicate(
            user_id=cast(int, request.user.pk),
            candidate=form.duplicate_candidate(),
            exclude_offer_id=cast(int, offer.pk),
        )
        if duplicate is not None:
            duplicate_kind = duplicate.kind
            confirmed = (
                duplicate.kind == "near"
                and form.cleaned_data["confirm_near_duplicate"]
                and request.POST.get("duplicate_offer_id") == str(duplicate.offer.pk)
            )
            if duplicate.kind == "exact" or not confirmed:
                if duplicate.kind == "exact":
                    form.add_error(None, "An exact duplicate Bonus Offer already exists.")
                else:
                    form.add_error(
                        None,
                        "A similar Bonus Offer already exists. Review it before saving.",
                    )
            else:
                duplicate = None

        if duplicate is None:
            try:
                with transaction.atomic():
                    current = BonusOffer.objects.select_for_update().get(
                        pk=offer.pk,
                        user=request.user,
                    )
                    previous_version = cast(int, before_snapshot["version"])
                    if current.version != previous_version or current.is_retired:
                        raise ValueError("bonus_offer_changed_during_edit")

                    BonusOfferRevision.objects.create(
                        offer=current,
                        changed_by=cast(User, request.user),
                        snapshot=before_snapshot,
                    )
                    updated = form.save(commit=False)
                    updated.version = current.version + 1
                    updated.save()
                    BonusOfferWorkInvalidator().invalidate(
                        cast(int, updated.pk),
                        current_version=updated.version,
                        now=timezone.now(),
                        removed=False,
                    )
            except ValueError as error:
                if str(error) == "bonus_offer_work_already_dispatched":
                    messages.error(
                        request,
                        "This Bonus Offer currently has dispatched work and cannot be changed yet.",
                    )
                    return redirect("bonus-offer-list")
                if str(error) == "bonus_offer_changed_during_edit":
                    messages.error(
                        request,
                        "This Bonus Offer changed while you were editing it. Review the latest version.",
                    )
                    return redirect("bonus-offer-list")
                raise

            messages.success(
                request,
                "Bonus Offer updated as Needs review."
                if updated.needs_review
                else "Bonus Offer updated.",
            )
            return redirect("bonus-offer-list")

    return render(
        request,
        "qbet_web/bonus_offer_edit.html",
        {
            "form": form,
            "offer": offer,
            "bonus_offer_duplicate": duplicate,
            "bonus_offer_duplicate_kind": duplicate_kind,
        },
    )


@login_required
@require_POST
def bonus_offer_remove(request: HttpRequest, offer_id: int) -> HttpResponse:
    try:
        with transaction.atomic():
            offer = get_object_or_404(
                BonusOffer.objects.select_for_update().select_related("provider"),
                pk=offer_id,
                user=request.user,
            )
            if offer.is_retired:
                messages.info(request, "Bonus Offer is already removed.")
                return redirect("bonus-offer-list")
            if offer.is_expired:
                messages.error(
                    request,
                    "Expired Bonus Offers are historical and do not need removal.",
                )
                return redirect("bonus-offer-list")

            before_snapshot = bonus_offer_snapshot(offer)
            BonusOfferRevision.objects.create(
                offer=offer,
                changed_by=cast(User, request.user),
                snapshot=before_snapshot,
            )
            retired_at = timezone.now()
            offer.version += 1
            offer.retired_at = retired_at
            offer.save(update_fields=("version", "retired_at", "updated_at"))
            BonusOfferWorkInvalidator().invalidate(
                cast(int, offer.pk),
                current_version=offer.version,
                now=retired_at,
                removed=True,
            )
    except ValueError as error:
        if str(error) == "bonus_offer_work_already_dispatched":
            messages.error(
                request,
                "This Bonus Offer currently has dispatched work and cannot be removed yet.",
            )
            return redirect("bonus-offer-list")
        raise

    messages.success(request, "Bonus Offer removed from future BonusEngine work.")
    return redirect("bonus-offer-list")


def _preserve_dialog(request: HttpRequest, form: BonusOfferForm) -> None:
    request.session[_FORM_DATA_KEY] = {
        name: request.POST.get(name, "")
        for name in form.fields
        if name != "confirm_near_duplicate"
    }
    request.session[_DIALOG_OPEN_KEY] = True


def _safe_return_path(request: HttpRequest) -> str:
    candidate = str(request.POST.get("return_to") or "").strip()
    if (
        candidate.startswith("/")
        and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidate
    return "/bonus-offers/"
