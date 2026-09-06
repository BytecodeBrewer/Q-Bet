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


class PortfolioLedgerRow(models.Model):
    """Durable snapshot for one isolated mode and currency context."""

    id = models.BigAutoField(primary_key=True)
    mode = models.CharField(max_length=16)
    currency = models.CharField(max_length=3)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_portfolio_ledgers"
        constraints = [
            models.UniqueConstraint(
                fields=("mode", "currency"),
                name="qbet_portfolio_ledger_mode_currency_unique",
            )
        ]


class ExecutionRecordRow(models.Model):
    """Durable approval and settlement lifecycle snapshot."""

    record_id = models.UUIDField(primary_key=True, editable=False)
    correlation_id = models.UUIDField(db_index=True)
    mode = models.CharField(max_length=16)
    state = models.CharField(max_length=32)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_execution_records"
        ordering = ("-updated_at",)


class RoutingConfigurationRow(models.Model):
    """Durable GUI-controlled engine and mode routing configuration."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_routing_configurations"


class ModeWorkQueueRow(models.Model):
    """Durable mode-specific dispatch queue and lifecycle history snapshot."""

    work_id = models.UUIDField(primary_key=True, editable=False)
    correlation_id = models.UUIDField(db_index=True)
    mode = models.CharField(max_length=16)
    state = models.CharField(max_length=16, db_index=True)
    scheduled_for = models.DateTimeField(db_index=True)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_mode_work_queue"
        ordering = ("scheduled_for", "work_id")
