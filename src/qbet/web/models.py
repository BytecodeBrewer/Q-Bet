from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class SimulationAvailability(models.Model):
    """Persisted global switch for the local simulation control plane."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_simulation_availability"


class SimulationRunState(models.Model):
    """Authoritative GUI-visible lifecycle state for one simulation run."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    run_id = models.UUIDField(primary_key=True, editable=False)
    engine = models.CharField(max_length=32)
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
    error_message = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_simulation_run_state"
        ordering = ("-updated_at",)

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.RUNNING}


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
