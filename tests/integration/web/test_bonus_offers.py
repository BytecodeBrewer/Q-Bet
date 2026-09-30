from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from qbet.data import THE_ODDS_API_PROVIDER_ID
from qbet.providers import GGL_WHITELIST_URL
from qbet.storage.models import SportsbookExternalIdentityRow, SportsbookProviderRow
from qbet.web.models import BonusOffer, BonusOfferRevision


_BROWSER_HELP_TEXTS = (
    "Describe the promotion terms. Q-Bet derives the supported calculation stage.",
    "The advertised reward or free-bet amount.",
    "Amount that must be wagered to earn the advertised reward.",
    "Leave empty if the promotion has no minimum-odds condition.",
    "Record the advertised turnover multiplier.",
    "Record any other material condition.",
)


def _browser_binary() -> str | None:
    return (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )


def _browser_probe(rendered_html: str, *, width: int, height: int) -> dict[str, Any]:
    browser = _browser_binary()
    if browser is None:
        raise RuntimeError("No Chromium/Chrome binary is available.")

    static_root = Path(settings.BASE_DIR, "static").resolve().as_uri().rstrip("/")
    html = (
        rendered_html.replace('href="/static/', f'href="{static_root}/')
        .replace('src="/static/', f'src="{static_root}/')
    )
    instrumentation = r"""
<script>
window.addEventListener("load", () => {
  setTimeout(() => {
    const visible = (element) => Boolean(
      element &&
      element.getClientRects().length &&
      getComputedStyle(element).visibility !== "hidden"
    );
    const forms = [...document.querySelectorAll("[data-bonus-offer-form]")];
    const visibleForm = forms.find(visible) || null;
    const fields = visibleForm?.querySelector("[data-bonus-offer-form-fields]") || null;
    const material = visibleForm?.querySelector("[data-bonus-material-conditions]") || null;
    const guidance = document.querySelector("[data-bonus-offer-guidance]");
    const entry = guidance?.closest(".bonus-offer-entry") || null;
    const addButton = entry?.querySelector("[data-bonus-offer-dialog-open]") || null;
    const actionButtons = [...document.querySelectorAll(".icon-action-button")];
    const helpTexts = visibleForm
      ? [...visibleForm.querySelectorAll(".bonus-offer-field small")]
          .filter(visible)
          .map((element) => element.textContent.trim())
      : [];
    const gridColumns = fields ? getComputedStyle(fields).gridTemplateColumns : "";
    const evidence = {
      viewportWidth: window.innerWidth,
      overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      guidanceVisible: visible(guidance),
      guidanceDismiss: Boolean(guidance?.querySelector("[data-notice-dismiss]")),
      guidanceBeforeAdd: Boolean(
        guidance &&
        addButton &&
        guidance.getBoundingClientRect().bottom <= addButton.getBoundingClientRect().top
      ),
      editLabel: document.querySelector('[aria-label^="Edit Bonus Offer"]')?.getAttribute("aria-label") || "",
      removeLabel: document.querySelector('[aria-label^="Remove Bonus Offer"]')?.getAttribute("aria-label") || "",
      actionSizes: actionButtons.map((element) => {
        const rect = element.getBoundingClientRect();
        return [Math.round(rect.width), Math.round(rect.height)];
      }),
      dialogOpen: Boolean(document.querySelector("[data-bonus-offer-dialog]")?.open),
      formVisible: visible(visibleForm),
      gridColumnCount: gridColumns.trim() ? gridColumns.trim().split(/\s+/).length : 0,
      helpTexts,
      personalNotesVisible: document.body.innerText.includes("Personal notes"),
      notesInputPresent: Boolean(document.querySelector('[name="notes"]')),
      materialVisible: visible(material),
      materialEnabled: material
        ? [...material.querySelectorAll("input, select, textarea")].every((field) => !field.disabled)
        : false,
    };
    const output = document.createElement("pre");
    output.id = "qbet-browser-evidence";
    output.textContent = JSON.stringify(evidence);
    document.body.appendChild(output);
  }, 80);
});
</script>
"""
    html = html.replace("</body>", instrumentation + "</body>")

    with tempfile.TemporaryDirectory(prefix="qbet-browser-") as tmp:
        html_path = Path(tmp, "rendered.html")
        html_path.write_text(html, encoding="utf-8")
        completed = subprocess.run(
            (
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--allow-file-access-from-files",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=1400",
                f"--window-size={width},{height}",
                "--dump-dom",
                html_path.resolve().as_uri(),
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )

    match = re.search(
        r'<pre id="qbet-browser-evidence">(.*?)</pre>',
        completed.stdout,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("Browser evidence marker was not rendered.")
    return json.loads(html_lib.unescape(match.group(1)))


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
        self.inactive_provider = SportsbookProviderRow.objects.create(
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

    def _bet_and_get_payload(self, **overrides: str) -> dict[str, str]:
        payload = {
            "provider": self.provider.provider_id,
            "name": "Weekend reward",
            "promotion_shape": BonusOffer.PromotionShape.BET_AND_GET,
            "promotion_value": "10.00",
            "currency": "EUR",
            "required_stake": "25.00",
            "minimum_odds": "1.50",
            "wagering_requirement": "",
            "stake_return_rule": "",
            "unsupported_terms": "",
            "valid_until": (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "return_to": "/bonus-offers/",
        }
        payload.update(overrides)
        return payload

    def test_authenticated_shell_exposes_promotion_first_dialog_and_eligible_provider_only(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get("/bonus-offers/")

        self.assertContains(response, "data-bonus-offer-menu-toggle")
        self.assertContains(response, "New Bonus Offer")
        self.assertContains(response, "Bonus Offer List")
        self.assertContains(response, "Licensed Book")
        self.assertNotContains(response, "Inactive Book")
        self.assertContains(response, "What does the sportsbook offer?")
        self.assertContains(response, "Bet &amp; get a free bet")
        self.assertContains(response, "Free bet already available")
        self.assertContains(
            response,
            "Describe the promotion terms. Q-Bet derives the supported calculation stage.",
        )
        self.assertContains(response, "The advertised reward or free-bet amount.")
        self.assertContains(
            response,
            "Amount that must be wagered to earn the advertised reward.",
        )
        self.assertContains(
            response,
            "Leave empty if the promotion has no minimum-odds condition.",
        )
        self.assertContains(response, "Record the advertised turnover multiplier.")
        self.assertContains(response, "Record any other material condition.")
        self.assertNotContains(response, "Personal notes")
        self.assertNotContains(response, 'name="notes"')
        self.assertNotContains(response, 'name="promotion_type"')

    def test_offer_management_uses_natural_guidance_before_new_offer_action(self) -> None:
        SportsbookExternalIdentityRow.objects.create(
            source_id=THE_ODDS_API_PROVIDER_ID,
            external_key="licensed-book-api",
            provider=self.provider,
        )
        BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="One usable promotion",
            promotion_shape=BonusOffer.PromotionShape.BET_AND_GET,
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            promotion_value="10.00",
            currency="EUR",
            required_stake="25.00",
            valid_until=timezone.now() + timedelta(days=2),
        )
        self.client.force_login(self.user)

        response = self.client.get("/bonus-offers/")
        body = response.content.decode()

        self.assertContains(response, "Only a few bonus offers are available")
        self.assertContains(
            response,
            "1 bonus offer(s) from 1 sportsbook(s) can currently be compared.",
        )
        self.assertNotContains(response, "Bonus coverage")
        self.assertNotContains(response, "provider coverage")
        self.assertNotContains(response, "usable active offer")
        self.assertNotContains(response, "Add promotion data")
        self.assertNotContains(response, "Manage Bonus Offers")
        self.assertContains(response, "data-bonus-offer-guidance")
        self.assertContains(response, "data-engine-notice")
        self.assertContains(response, "data-notice-dismiss")
        self.assertContains(response, "Info · Bonus offers")
        self.assertContains(response, "qbet_web/engine_notices.js")
        entry_start = body.index('<section class="bonus-offer-entry"')
        entry_end = body.index("</section>", entry_start)
        entry = body[entry_start:entry_end]
        self.assertLess(
            entry.index("Only a few bonus offers are available"),
            entry.index("New Bonus Offer"),
        )

    def test_real_browser_renders_management_create_and_edit_with_real_helper_copy(self) -> None:
        browser = _browser_binary()
        if browser is None:
            if os.environ.get("CI"):
                self.fail("CI must provide Chromium/Chrome for Bonus Offer browser evidence.")
            self.skipTest("Chromium/Chrome is not available in this local environment.")

        SportsbookExternalIdentityRow.objects.create(
            source_id=THE_ODDS_API_PROVIDER_ID,
            external_key="licensed-book-browser-api",
            provider=self.provider,
        )
        offer = BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Browser evidence promotion",
            promotion_shape=BonusOffer.PromotionShape.BET_AND_GET,
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            promotion_value="10.00",
            currency="EUR",
            required_stake="25.00",
            minimum_odds="1.50",
            valid_until=timezone.now() + timedelta(days=2),
        )
        self.client.force_login(self.user)

        management = self.client.get("/bonus-offers/")
        management_html = management.content.decode("utf-8")
        for width, height in ((1280, 900), (390, 844)):
            evidence = _browser_probe(management_html, width=width, height=height)
            self.assertTrue(evidence["guidanceVisible"])
            self.assertTrue(evidence["guidanceDismiss"])
            self.assertTrue(evidence["guidanceBeforeAdd"])
            self.assertEqual(evidence["editLabel"], "Edit Bonus Offer Browser evidence promotion")
            self.assertEqual(
                evidence["removeLabel"],
                "Remove Bonus Offer Browser evidence promotion",
            )
            self.assertEqual(evidence["actionSizes"], [[38, 38], [38, 38]])
            self.assertLessEqual(int(evidence["overflowX"]), 1)
            self.assertFalse(evidence["personalNotesVisible"])
            self.assertFalse(evidence["notesInputPresent"])

        session = self.client.session
        session["qbet.bonus_offer.dialog_open"] = True
        session.save()
        create = self.client.get("/bonus-offers/")
        create_html = create.content.decode("utf-8")
        create_desktop = _browser_probe(create_html, width=1280, height=1100)
        create_mobile = _browser_probe(create_html, width=390, height=1100)

        for evidence in (create_desktop, create_mobile):
            self.assertTrue(evidence["dialogOpen"])
            self.assertTrue(evidence["formVisible"])
            self.assertTrue(evidence["materialVisible"])
            self.assertTrue(evidence["materialEnabled"])
            self.assertLessEqual(int(evidence["overflowX"]), 1)
            self.assertFalse(evidence["personalNotesVisible"])
            self.assertFalse(evidence["notesInputPresent"])
            for helper in _BROWSER_HELP_TEXTS:
                self.assertTrue(
                    any(helper in text for text in evidence["helpTexts"]),
                    msg=f"Missing visible helper text: {helper}",
                )
        self.assertEqual(create_desktop["gridColumnCount"], 2)
        self.assertEqual(create_mobile["gridColumnCount"], 1)

        edit = self.client.get(f"/bonus-offers/{offer.pk}/edit/")
        edit_html = edit.content.decode("utf-8")
        edit_desktop = _browser_probe(edit_html, width=1280, height=1500)
        edit_mobile = _browser_probe(edit_html, width=390, height=1800)

        for evidence in (edit_desktop, edit_mobile):
            self.assertTrue(evidence["formVisible"])
            self.assertTrue(evidence["materialVisible"])
            self.assertTrue(evidence["materialEnabled"])
            self.assertLessEqual(int(evidence["overflowX"]), 1)
            self.assertFalse(evidence["personalNotesVisible"])
            self.assertFalse(evidence["notesInputPresent"])
            for helper in _BROWSER_HELP_TEXTS:
                self.assertTrue(
                    any(helper in text for text in evidence["helpTexts"]),
                    msg=f"Missing visible helper text: {helper}",
                )
        self.assertEqual(edit_desktop["gridColumnCount"], 2)
        self.assertEqual(edit_mobile["gridColumnCount"], 1)

    def test_create_bet_and_get_derives_internal_qualifying_stage(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            "/bonus-offers/create/",
            self._bet_and_get_payload(),
        )

        self.assertEqual(response.status_code, 302)
        offer = BonusOffer.objects.get()
        self.assertEqual(offer.user, self.user)
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.promotion_shape, BonusOffer.PromotionShape.BET_AND_GET)
        self.assertEqual(offer.promotion_type, BonusOffer.PromotionType.QUALIFYING_BET)
        self.assertEqual(offer.required_stake, 25)
        self.assertEqual(offer.promotion_value, 10)
        self.assertFalse(offer.is_expired)

        rendered = self.client.get("/bonus-offers/")
        self.assertContains(
            rendered,
            "Stage 1: qualifying wager → Stage 2: free bet after sportsbook credit",
        )
        self.assertContains(rendered, "Current automated stage: Qualifying wager")

    def test_bonus_offer_list_is_user_scoped_and_keeps_expired_history(self) -> None:
        BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Expired history",
            promotion_shape=BonusOffer.PromotionShape.FREE_BET,
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
        self.assertNotContains(response, "Edit promotion terms")

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
                "promotion_shape": BonusOffer.PromotionShape.FREE_BET,
                "promotion_value": "20.00",
                "currency": "EUR",
                "required_stake": "",
                "minimum_odds": "1.80",
                "wagering_requirement": "",
                "stake_return_rule": "",
                "unsupported_terms": "",
                "valid_until": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "return_to": "/bonus-offers/",
            },
            follow=True,
        )

        self.assertEqual(BonusOffer.objects.count(), 0)
        self.assertContains(response, 'data-open="true"')
        self.assertContains(response, "Needs stake rule")
        self.assertContains(response, "Choose whether the promotional stake is returned.")

    def test_unsupported_material_condition_is_saved_needs_review_not_ignored(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            "/bonus-offers/create/",
            self._bet_and_get_payload(
                name="Turnover promo",
                wagering_requirement="3",
            ),
        )

        self.assertEqual(response.status_code, 302)
        offer = BonusOffer.objects.get()
        self.assertTrue(offer.needs_review)
        self.assertEqual(offer.status_label, "Needs review")
        self.assertIn("Turnover requirements", offer.unsupported_reason)

    def test_supported_shape_keeps_material_conditions_visible_and_fails_closed(self) -> None:
        self.client.force_login(self.user)

        rendered = self.client.get("/bonus-offers/")
        self.assertContains(rendered, "Other material conditions")
        self.assertContains(rendered, "data-bonus-material-conditions")
        self.assertContains(rendered, 'name="unsupported_terms"')
        self.assertNotContains(
            rendered,
            'data-bonus-offer-shapes="other"',
        )

        response = self.client.post(
            "/bonus-offers/create/",
            self._bet_and_get_payload(
                name="Accumulator-limited reward",
                unsupported_terms="Reward applies only to a specified accumulator.",
            ),
        )

        self.assertEqual(response.status_code, 302)
        offer = BonusOffer.objects.get()
        self.assertEqual(
            offer.unsupported_terms,
            "Reward applies only to a specified accumulator.",
        )
        self.assertTrue(offer.needs_review)
        self.assertEqual(offer.status_label, "Needs review")
        self.assertIn("material promotion conditions", offer.unsupported_reason)

    def test_other_shape_requires_explanation_and_never_derives_strategy(self) -> None:
        self.client.force_login(self.user)
        payload = self._bet_and_get_payload(
            name="Cashback condition",
            promotion_shape=BonusOffer.PromotionShape.OTHER,
            promotion_value="",
            required_stake="",
            unsupported_terms="Cashback depends on a losing accumulator.",
        )
        response = self.client.post("/bonus-offers/create/", payload)

        self.assertEqual(response.status_code, 302)
        offer = BonusOffer.objects.get()
        self.assertEqual(offer.promotion_type, "")
        self.assertEqual(offer.status_label, "Needs review")

    def test_exact_duplicate_is_rejected_clearly(self) -> None:
        self.client.force_login(self.user)
        payload = self._bet_and_get_payload()
        self.client.post("/bonus-offers/create/", payload)

        response = self.client.post("/bonus-offers/create/", payload, follow=True)

        self.assertEqual(BonusOffer.objects.count(), 1)
        self.assertContains(response, "Exact duplicate already exists")
        self.assertContains(response, "exact duplicate and was not saved")

    def test_near_duplicate_requires_review_then_allows_deliberate_override(self) -> None:
        self.client.force_login(self.user)
        first_payload = self._bet_and_get_payload()
        self.client.post("/bonus-offers/create/", first_payload)
        existing = BonusOffer.objects.get()

        near_payload = self._bet_and_get_payload(
            name="Weekend reward updated wording",
            valid_until=(timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
        )
        response = self.client.post("/bonus-offers/create/", near_payload, follow=True)

        self.assertEqual(BonusOffer.objects.count(), 1)
        self.assertContains(response, "Similar Bonus Offer found")
        self.assertContains(response, "Review existing offer")

        override = {
            **near_payload,
            "confirm_near_duplicate": "on",
            "duplicate_offer_id": str(existing.pk),
        }
        response = self.client.post("/bonus-offers/create/", override)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(BonusOffer.objects.count(), 2)

    def test_edit_is_owner_scoped_and_preserves_before_snapshot(self) -> None:
        self.client.force_login(self.user)
        self.client.post("/bonus-offers/create/", self._bet_and_get_payload())
        offer = BonusOffer.objects.get()

        edit_payload = self._bet_and_get_payload(name="Corrected promotion title")
        response = self.client.post(
            f"/bonus-offers/{offer.pk}/edit/",
            edit_payload,
        )

        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertEqual(offer.name, "Corrected promotion title")
        self.assertEqual(offer.version, 2)
        revision = BonusOfferRevision.objects.get(offer=offer)
        self.assertEqual(revision.changed_by, self.user)
        self.assertEqual(revision.snapshot["name"], "Weekend reward")
        self.assertEqual(revision.snapshot["version"], 1)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/bonus-offers/{offer.pk}/edit/").status_code, 404)

    def test_edit_renders_existing_deadline_in_datetime_local_format(self) -> None:
        deadline = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        offer = BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Deadline edit",
            promotion_shape=BonusOffer.PromotionShape.BET_AND_GET,
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            promotion_value="10.00",
            currency="EUR",
            required_stake="25.00",
            valid_until=deadline,
        )
        self.client.force_login(self.user)

        response = self.client.get(f"/bonus-offers/{offer.pk}/edit/")

        expected = timezone.localtime(deadline).strftime("%Y-%m-%dT%H:%M")
        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(response, f'value="{expected}"')
        self.assertContains(
            response,
            "Describe the promotion terms. Q-Bet derives the supported calculation stage.",
        )
        self.assertContains(response, "The advertised reward or free-bet amount.")
        self.assertContains(
            response,
            "Amount that must be wagered to earn the advertised reward.",
        )
        self.assertContains(
            response,
            "Leave empty if the promotion has no minimum-odds condition.",
        )
        self.assertContains(response, "Record the advertised turnover multiplier.")
        self.assertContains(response, "Record any other material condition.")
        self.assertNotContains(response, "Personal notes")
        self.assertNotContains(response, 'name="notes"')

    def test_active_offer_with_now_inactive_provider_remains_editable(self) -> None:
        deadline = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        offer = BonusOffer.objects.create(
            user=self.user,
            provider=self.inactive_provider,
            name="Provider became inactive",
            promotion_shape=BonusOffer.PromotionShape.BET_AND_GET,
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            promotion_value="10.00",
            currency="EUR",
            required_stake="25.00",
            valid_until=deadline,
        )
        self.client.force_login(self.user)

        rendered = self.client.get(f"/bonus-offers/{offer.pk}/edit/")
        self.assertContains(rendered, "Inactive Book")
        self.assertContains(
            rendered,
            (
                f'<option value="{self.inactive_provider.provider_id}" selected>'
                "Inactive Book</option>"
            ),
            html=True,
        )

        response = self.client.post(
            f"/bonus-offers/{offer.pk}/edit/",
            {
                "provider": self.inactive_provider.provider_id,
                "name": "Corrected inactive-provider promotion",
                "promotion_shape": BonusOffer.PromotionShape.BET_AND_GET,
                "promotion_value": "10.00",
                "currency": "EUR",
                "required_stake": "25.00",
                "minimum_odds": "",
                "wagering_requirement": "",
                "stake_return_rule": "",
                "unsupported_terms": "",
                "valid_until": deadline.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertEqual(response.status_code, 302)
        offer.refresh_from_db()
        self.assertEqual(offer.provider, self.inactive_provider)
        self.assertEqual(offer.name, "Corrected inactive-provider promotion")
        self.assertEqual(offer.status_label, "Unavailable")
        self.assertEqual(BonusOfferRevision.objects.filter(offer=offer).count(), 1)

    def test_remove_archives_offer_preserves_history_and_allows_fresh_reentry(self) -> None:
        self.client.force_login(self.user)
        payload = self._bet_and_get_payload(name="Removable promotion")
        self.client.post("/bonus-offers/create/", payload)
        offer = BonusOffer.objects.get()

        listed = self.client.get("/bonus-offers/")
        self.assertContains(listed, f"/bonus-offers/{offer.pk}/edit/")
        self.assertContains(listed, f"/bonus-offers/{offer.pk}/remove/")
        self.assertContains(
            listed,
            'aria-label="Edit Bonus Offer Removable promotion"',
        )
        self.assertContains(
            listed,
            'aria-label="Remove Bonus Offer Removable promotion"',
        )
        self.assertContains(listed, 'title="Edit Bonus Offer"')
        self.assertContains(listed, 'title="Remove Bonus Offer"')
        self.assertContains(listed, 'class="icon-action-button"')
        self.assertContains(
            listed,
            'class="icon-action-button icon-action-button-danger"',
        )
        self.assertContains(
            listed,
            f'method="post" action="/bonus-offers/{offer.pk}/remove/"',
        )
        self.assertContains(listed, 'name="csrfmiddlewaretoken"')

        response = self.client.post(
            f"/bonus-offers/{offer.pk}/remove/",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        offer.refresh_from_db()
        self.assertTrue(offer.is_retired)
        self.assertEqual(offer.version, 2)
        self.assertEqual(offer.status_label, "Removed")
        revision = BonusOfferRevision.objects.get(offer=offer)
        self.assertEqual(revision.snapshot["version"], 1)
        self.assertIsNone(revision.snapshot["retired_at"])
        self.assertContains(response, "Bonus Offer removed from future BonusEngine work.")
        self.assertNotContains(response, f"/bonus-offers/{offer.pk}/edit/")
        self.assertNotContains(response, f"/bonus-offers/{offer.pk}/remove/")

        recreated = self.client.post("/bonus-offers/create/", payload)
        self.assertEqual(recreated.status_code, 302)
        self.assertEqual(BonusOffer.objects.filter(name="Removable promotion").count(), 2)

    def test_expired_offer_cannot_be_edited(self) -> None:
        offer = BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Expired",
            promotion_shape=BonusOffer.PromotionShape.FREE_BET,
            promotion_type=BonusOffer.PromotionType.FREE_BET,
            promotion_value="10.00",
            currency="EUR",
            stake_return_rule=BonusOffer.StakeReturnRule.STAKE_NOT_RETURNED,
            valid_until=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.user)

        response = self.client.get(f"/bonus-offers/{offer.pk}/edit/", follow=True)

        self.assertContains(response, "Expired Bonus Offers are historical and cannot be edited.")
        self.assertEqual(BonusOfferRevision.objects.count(), 0)
