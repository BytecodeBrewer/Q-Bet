from __future__ import annotations

from django.db import models


class SimulationRecordRow(models.Model):
    """Durable structured simulation log record stored in PostgreSQL."""

    run_id = models.UUIDField()
    sequence = models.BigIntegerField()
    payload = models.TextField()

    class Meta:
        db_table = "qbet_simulation_records"
        constraints = [
            models.UniqueConstraint(
                fields=("run_id", "sequence"),
                name="qbet_simulation_records_run_sequence_unique",
            )
        ]
        ordering = ("sequence",)


class SimulationReportRow(models.Model):
    """Compact durable simulation report stored in PostgreSQL."""

    run_id = models.UUIDField(primary_key=True, editable=False)
    generated_at = models.DateTimeField(db_index=True)
    payload = models.TextField()

    class Meta:
        db_table = "qbet_simulation_reports"
        ordering = ("-generated_at",)


class ProviderStateRow(models.Model):
    """Durable provider/account operational state used by Domain Risk."""

    provider_id = models.CharField(max_length=255, primary_key=True)
    active_bets_count = models.PositiveIntegerField(default=0)
    last_bet_timestamp = models.DateTimeField(null=True, blank=True)
    is_cooldown_active = models.BooleanField(default=False)

    class Meta:
        db_table = "qbet_provider_states"
