from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0008_sandbox_funding_outcomes")]

    operations = [
        migrations.CreateModel(
            name="NotificationPreferenceRow",
            fields=[
                ("user_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("email_enabled", models.BooleanField(default=True)),
                ("inbox_enabled", models.BooleanField(default=True)),
                ("categories", models.JSONField(default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_notification_preferences"},
        ),
        migrations.CreateModel(
            name="NotificationInboxReadRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id", models.CharField(max_length=255)),
                ("task_id", models.UUIDField()),
                ("read_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "qbet_notification_inbox_reads", "ordering": ("-read_at",)},
        ),
        migrations.AddConstraint(
            model_name="notificationinboxreadrow",
            constraint=models.UniqueConstraint(
                fields=("user_id", "task_id"), name="qbet_notification_inbox_read_unique"
            ),
        ),
    ]
