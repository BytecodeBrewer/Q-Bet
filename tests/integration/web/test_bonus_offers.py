from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from qbet.providers import GGL_WHITELIST_URL
from qbet.storage.models import SportsbookProviderRow
from qbet.web.models import BonusOffer


class BonusOfferWebTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("offer-user", password="Strong-pass-123")
        self.other = User.objects.create_user("other-user", password="Strong-pass-123")
        self.provider = SportsbookProviderRow.objects.create(
            provider_id="licensed-book",
            legal_name="Licensed Book GmbH",
            display_name="Licensed Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url=GGL_WHITELIST_URL,
            whitelist_snapshot_date=date(2026, 9, 7),
            status="active",
        )
        SportsbookProviderRow.objects.create(
            provider_id="inactive-book",
            legal_name="Inactive Book GmbH",
            display_name="Inactive Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url=GGL_WHITELIST_URL,
            whitelist_snapshot_date=date(2026, 9, 7),
            status="inactive",
        )

    def test_authenticated_shell_exposes_add_menu_dialog_and_eligible_provider_only(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get("/bonus-offers/")

        self.assertContains(response, "data-bonus-offer-menu-toggle")
        self.assertContains(response, "New Bonus Offer")
        self.assertContains(response, "Bonus Offer List")
        self.assertContains(response, "Licensed Book")
        self.assertNotContains(response, "Inactive Book")

    def test_create_qualifying_offer_is_owned_by_authenticated_user(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            "/bonus-offers/create/",
            {
                "provider": self.provider.provider_id,
                "name": "Weekend qualifier",
                "promotion_type": BonusOffer.PromotionType.QUALIFYING_BET,
                "promotion_value": "",
                "currency": "EUR",
                "required_stake": "25.00",
                "minimum_odds": "1.50",
                "wagering_requirement": "",
                "stake_return_rule": "",
                "valid_until": (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "notes": "Account-specific terms only.",
                "return_to": "/bonus-offers/",
            },
        )

        self.assertEqual(response.status_code, 302)
        offer = BonusOffer.objects.get()
        self.assertEqual(offer.user, self.user)
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.required_stake, 25)
        self.assertFalse(offer.is_expired)

    def test_bonus_offer_list_is_user_scoped_and_keeps_expired_history(self) -> None:
        BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Expired history",
            promotion_type=BonusOffer.PromotionType.FREE_BET,
            promotion_value="10.00",
            currency="EUR",
            stake_return_rule=BonusOffer.StakeReturnRule.STAKE_NOT_RETURNED,
            valid_until=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.user)
        response = self.client.get("/bonus-offers/")
        self.assertContains(response, "Expired history")
        self.assertContains(response, "Expired")

        self.client.force_login(self.other)
        response = self.client.get("/bonus-offers/")
        self.assertNotContains(response, "Expired history")

    def test_invalid_free_bet_preserves_input_and_reopens_dialog(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            "/bonus-offers/create/",
            {
                "provider": self.provider.provider_id,
                "name": "Needs stake rule",
                "promotion_type": BonusOffer.PromotionType.FREE_BET,
                "promotion_value": "20.00",
                "currency": "EUR",
                "required_stake": "",
                "minimum_odds": "1.80",
                "wagering_requirement": "",
                "stake_return_rule": "",
                "valid_until": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "notes": "",
                "return_to": "/bonus-offers/",
            },
            follow=True,
        )

        self.assertEqual(BonusOffer.objects.count(), 0)
        self.assertContains(response, 'data-open="true"')
        self.assertContains(response, "Needs stake rule")
        self.assertContains(response, "Choose whether the promotional stake is returned.")
