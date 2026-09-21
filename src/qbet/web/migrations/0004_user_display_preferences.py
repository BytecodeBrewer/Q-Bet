from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("web", "0003_customer_report_access"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserDisplayPreference",
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
                ("language", models.CharField(default="de", max_length=8)),
                ("region", models.CharField(default="DE", max_length=8)),
                ("timezone_name", models.CharField(default="Europe/Berlin", max_length=64)),
                ("time_format", models.CharField(default="24h", max_length=8)),
                ("currency", models.CharField(default="EUR", max_length=3)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qbet_display_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "qbet_user_display_preferences"},
        ),
    ]