from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0005_harden_monitoring_records")]

    operations = [
        migrations.CreateModel(
            name="NotificationTaskRow",
            fields=[
                ("task_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("execution_id", models.UUIDField(db_index=True)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("recipient_id", models.CharField(max_length=255)),
                ("state", models.CharField(db_index=True, max_length=32)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_notification_tasks",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="notificationtaskrow",
            constraint=models.UniqueConstraint(
                fields=("execution_id", "recipient_id"),
                name="qbet_notification_execution_recipient_unique",
            ),
        ),
    ]
