from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0003_mode_work_queue")]

    operations = [
        migrations.CreateModel(
            name="MonitoringRecordRow",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("payload", models.JSONField()),
            ],
            options={
                "db_table": "qbet_monitoring_records",
                "ordering": ("occurred_at", "id"),
            },
        )
    ]
