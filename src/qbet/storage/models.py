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


class MonitoringRecordRow(models.Model):
    """Append-only, redacted technical activity for the administrator Monitoring plane."""

    id = models.BigAutoField(primary_key=True)
    correlation_id = models.UUIDField(db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    payload = models.JSONField()

    class Meta:
        db_table = "qbet_monitoring_records"
        ordering = ("occurred_at", "id")


class NotificationTaskRow(models.Model):
    """Durable customer notification state keyed by execution and recipient."""

    task_id = models.UUIDField(primary_key=True, editable=False)
    execution_id = models.UUIDField(db_index=True)
    correlation_id = models.UUIDField(db_index=True)
    recipient_id = models.CharField(max_length=255)
    state = models.CharField(max_length=32, db_index=True)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_notification_tasks"
        constraints = [
            models.UniqueConstraint(
                fields=("execution_id", "recipient_id"),
                name="qbet_notification_execution_recipient_unique",
            )
        ]
        ordering = ("-updated_at",)


class PollingStrategyRow(models.Model):
    """Durable provider/target Smart Polling strategy configuration."""

    id = models.BigAutoField(primary_key=True)
    provider_id = models.CharField(max_length=255)
    source_id = models.CharField(max_length=255)
    target = models.CharField(max_length=16)
    engine = models.CharField(max_length=64, blank=True, default="")
    enabled = models.BooleanField(default=True)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_polling_strategies"
        constraints = [
            models.UniqueConstraint(
                fields=("provider_id", "source_id", "target", "engine"),
                name="qbet_polling_strategy_identity_unique",
            )
        ]
        ordering = ("provider_id", "source_id", "target", "engine")


class SandboxFundingOutcomeRow(models.Model):
    """Durable bunq sandbox provider outcome and Simulation ledger feedback state."""

    proposal_id = models.UUIDField(primary_key=True, editable=False)
    correlation_id = models.UUIDField(db_index=True)
    provider_id = models.CharField(max_length=64)
    sent = models.BooleanField()
    provider_reference = models.CharField(max_length=255, null=True, blank=True)
    reason_code = models.CharField(max_length=255, null=True, blank=True)
    ledger_applied = models.BooleanField(default=False)
    payload = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_sandbox_funding_outcomes"
        ordering = ("-updated_at",)


class NotificationPreferenceRow(models.Model):
    """Per-recipient durable notification channel and category choices."""

    user_id = models.CharField(max_length=255, primary_key=True)
    email_enabled = models.BooleanField(default=True)
    inbox_enabled = models.BooleanField(default=True)
    categories = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qbet_notification_preferences"


class NotificationInboxReadRow(models.Model):
    """Per-recipient read marker; it never changes the execution task itself."""

    user_id = models.CharField(max_length=255)
    task_id = models.UUIDField()
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qbet_notification_inbox_reads"
        constraints = [
            models.UniqueConstraint(
                fields=("user_id", "task_id"),
                name="qbet_notification_inbox_read_unique",
            )
        ]
        ordering = ("-read_at",)
