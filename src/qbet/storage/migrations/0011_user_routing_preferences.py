from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0010_notification_inbox_deliveries")]

    operations = [
        migrations.CreateModel(
            name="UserRoutingPreferenceRow",
            fields=[
                (
                    "user_id",
                    models.CharField(max_length=255, primary_key=True, serialize=False),
                ),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_user_routing_preferences"},
        )
    ]
