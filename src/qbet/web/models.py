from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from qbet.data import THE_ODDS_API_PROVIDER_ID
from qbet.provider_accounts import ProviderAccountSource, ProviderAccountStatus
from qbet.providers import GERMAN_JURISDICTION, ProviderStatus


class SimulationAvailability(models.Model):
    """Persisted global switch for the local simulation control plane."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_simulation_availability"


class SimulationPortfolioState(models.Model):
    """Seed definition for the single shared virtual Simulation portfolio."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    seed_balances = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_simulation_portfolio_state"


class SimulationPortfolioResetArchive(models.Model):
    """Prior Simulation ledger snapshots retained when the sandbox is reset."""

    portfolio_payload = models.JSONField()
    reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="qbet_simulation_portfolio_resets",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qbet_simulation_portfolio_reset_archives"
        ordering = ("-created_at", "-id")


class SimulationRunState(models.Model):
    """Authoritative GUI-visible lifecycle state for one simulation run."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    run_id = models.UUIDField(primary_key=True, editable=False)
    engine = models.CharField(max_length=32)
    portfolio_currency = models.CharField(max_length=3, default="EUR")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    progress = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        default=Decimal("0"),
    )
    current_capital = models.DecimalField(
        max_digits=24,
        decimal_places=8,
        default=Decimal("0"),
    )
    report_id = models.UUIDField(null=True, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="qbet_simulation_runs",
        null=True,
        blank=True,
    )
    error_message = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_simulation_run_state"
        ordering = ("-updated_at",)

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.RUNNING, self.Status.PROCESSING}


class CustomerReportAccess(models.Model):
    """Explicit per-user permission for one customer-facing result report."""

    report_id = models.UUIDField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_customer_report_accesses",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qbet_customer_report_access"
        constraints = [
            models.UniqueConstraint(
                fields=("report_id", "user"),
                name="qbet_customer_report_access_unique",
            )
        ]
        ordering = ("-granted_at",)


class PortfolioLedgerAccess(models.Model):
    """Explicit per-user permission for one shared authoritative ledger context."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_portfolio_ledger_accesses",
    )
    mode = models.CharField(max_length=16)
    currency = models.CharField(max_length=3)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qbet_portfolio_ledger_access"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "mode", "currency"),
                name="qbet_portfolio_ledger_access_unique",
            )
        ]
        ordering = ("mode", "currency")


class PortfolioCapitalLocation(models.Model):
    """User-owned allocation of authoritative ledger capital to one provider location."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_portfolio_capital_locations",
    )
    provider = models.ForeignKey(
        "storage.SportsbookProviderRow",
        on_delete=models.PROTECT,
        related_name="portfolio_capital_locations",
    )
    mode = models.CharField(max_length=16)
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    note = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_portfolio_capital_location"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "provider", "mode", "currency"),
                name="qbet_portfolio_capital_location_unique",
            )
        ]
        ordering = ("provider__display_name", "currency")


class ProviderAccount(models.Model):
    """User-owned sportsbook-account state without credentials or capital authority."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_provider_accounts",
    )
    provider = models.ForeignKey(
        "storage.SportsbookProviderRow",
        on_delete=models.PROTECT,
        related_name="user_accounts",
    )
    status = models.CharField(
        max_length=32,
        choices=tuple((value.value, value.value.replace("_", " ").title()) for value in ProviderAccountStatus if value is not ProviderAccountStatus.NOT_CONFIGURED),
        default=ProviderAccountStatus.DECLARED.value,
    )
    source = models.CharField(
        max_length=16,
        choices=tuple((value.value, value.value.title()) for value in ProviderAccountSource),
        default=ProviderAccountSource.MANUAL.value,
    )
    nickname = models.CharField(max_length=80, blank=True, default="")
    notes = models.CharField(max_length=500, blank=True, default="")
    configured_at = models.DateTimeField(auto_now_add=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_provider_accounts"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "provider"),
                name="qbet_provider_account_user_provider_unique",
            )
        ]
        ordering = ("provider__display_name", "provider_id")


class UserDisplayPreference(models.Model):
    """Durable user-owned regional display choices with no business authority."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_display_preferences",
    )
    language = models.CharField(max_length=8, default="de")
    region = models.CharField(max_length=8, default="DE")
    timezone_name = models.CharField(max_length=64, default="Europe/Berlin")
    time_format = models.CharField(max_length=8, default="24h")
    currency = models.CharField(max_length=3, default="EUR")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_user_display_preferences"


class UserAvatar(models.Model):
    """User-owned pointer to a normalized image in durable object storage."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_avatar",
    )
    object_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=32, default="image/jpeg")
    byte_size = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_user_avatars"


class AccountVerification(models.Model):
    """Verification state for newly registered accounts."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_account_verification",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "qbet_account_verification"


class BonusOffer(models.Model):
    """User-owned promotion terms that may feed BonusEngine preparation."""

    class PromotionType(models.TextChoices):
        QUALIFYING_BET = "qualifying_bet", "Qualifying bet"
        FREE_BET = "free_bet", "Free bet"

    class StakeReturnRule(models.TextChoices):
        STAKE_NOT_RETURNED = "stake_not_returned", "Stake not returned"
        STAKE_RETURNED = "stake_returned", "Stake returned"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qbet_bonus_offers",
    )
    provider = models.ForeignKey(
        "storage.SportsbookProviderRow",
        on_delete=models.PROTECT,
        related_name="bonus_offers",
    )
    name = models.CharField(max_length=160)
    promotion_type = models.CharField(max_length=32, choices=PromotionType.choices)
    promotion_value = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(
        max_length=3,
        choices=(("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD")),
        default="EUR",
    )
    required_stake = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    minimum_odds = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("1.01"))],
    )
    wagering_requirement = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    stake_return_rule = models.CharField(
        max_length=32,
        choices=StakeReturnRule.choices,
        blank=True,
        default="",
    )
    valid_until = models.DateTimeField(db_index=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_bonus_offers"
        ordering = ("valid_until", "-updated_at", "id")

    @property
    def is_expired(self) -> bool:
        return self.valid_until <= timezone.now()

    @property
    def is_preparation_ready(self) -> bool:
        if self.is_expired:
            return False
        provider = self.provider
        if (
            provider.status != ProviderStatus.ACTIVE.value
            or provider.jurisdiction != GERMAN_JURISDICTION
            or not provider.sports_betting
            or not provider.online
        ):
            return False
        if self.wagering_requirement not in (None, Decimal(0)):
            return False
        if self.promotion_type == self.PromotionType.QUALIFYING_BET:
            if (
                self.required_stake is None
                or self.promotion_value is not None
                or self.stake_return_rule
            ):
                return False
        elif self.promotion_type == self.PromotionType.FREE_BET:
            if (
                self.promotion_value is None
                or self.required_stake is not None
                or not self.stake_return_rule
            ):
                return False
        else:
            return False
        from qbet.storage.models import SportsbookExternalIdentityRow

        return SportsbookExternalIdentityRow.objects.filter(
            provider_id=provider.provider_id,
            source_id=THE_ODDS_API_PROVIDER_ID,
        ).exists()

    @property
    def status_label(self) -> str:
        if self.is_expired:
            return "Expired"
        return "Active" if self.is_preparation_ready else "Unavailable"
