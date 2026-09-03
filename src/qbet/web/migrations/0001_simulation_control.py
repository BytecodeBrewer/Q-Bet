from __future__ import annotations

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="SimulationAvailability",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("enabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_simulation_availability"},
        ),
        migrations.CreateModel(
            name="SimulationRunState",
            fields=[
                (
                    "run_id",
                    models.UUIDField(
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("engine", models.CharField(max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("stopped", "Stopped"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "progress",
                    models.DecimalField(
                        decimal_places=5,
                        default=Decimal("0"),
                        max_digits=6,
                    ),
                ),
                (
                    "current_capital",
                    models.DecimalField(
                        decimal_places=8,
                        default=Decimal("0"),
                        max_digits=24,
                    ),
                ),
                ("report_id", models.UUIDField(blank=True, null=True)),
                ("error_message", models.CharField(blank=True, max_length=255)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_simulation_run_state",
                "ordering": ("-updated_at",),
            },
        ),
    ]
