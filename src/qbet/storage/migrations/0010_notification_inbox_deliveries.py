from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0009_notification_preferences_inbox")]

    operations = [
        migrations.CreateModel(
            name="NotificationInboxDeliveryRow",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("user_id", models.CharField(max_length=255)),
                ("task_id", models.UUIDField()),
                ("category", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "qbet_notification_inbox_deliveries",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="notificationinboxdeliveryrow",
            constraint=models.UniqueConstraint(
                fields=("user_id", "task_id"),
                name="qbet_notification_inbox_delivery_unique",
            ),
        ),
    ]
