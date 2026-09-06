from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("web", "0002_protect_last_active_superuser"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerReportAccess",
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
                ("report_id", models.UUIDField()),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qbet_customer_report_accesses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "qbet_customer_report_access",
                "ordering": ("-granted_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="customerreportaccess",
            constraint=models.UniqueConstraint(
                fields=("report_id", "user"),
                name="qbet_customer_report_access_unique",
            ),
        ),
    ]
