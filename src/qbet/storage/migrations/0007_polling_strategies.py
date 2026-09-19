from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0006_notification_tasks")]

    operations = [
        migrations.CreateModel(
            name="PollingStrategyRow",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("provider_id", models.CharField(max_length=255)),
                ("source_id", models.CharField(max_length=255)),
                ("target", models.CharField(max_length=16)),
                ("engine", models.CharField(blank=True, default="", max_length=64)),
                ("enabled", models.BooleanField(default=True)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_polling_strategies",
                "ordering": ("provider_id", "source_id", "target", "engine"),
            },
        ),
        migrations.AddConstraint(
            model_name="pollingstrategyrow",
            constraint=models.UniqueConstraint(
                fields=("provider_id", "source_id", "target", "engine"),
                name="qbet_polling_strategy_identity_unique",
            ),
        ),
    ]
