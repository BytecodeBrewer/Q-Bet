from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0002_ledger_execution")]

    operations = [
        migrations.CreateModel(
            name="ModeWorkQueueRow",
            fields=[
                ("work_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("mode", models.CharField(max_length=16)),
                ("state", models.CharField(db_index=True, max_length=16)),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_mode_work_queue",
                "ordering": ("scheduled_for", "work_id"),
            },
        )
    ]
