from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("web", "0008_bonus_offer"),
    ]

    operations = [
        migrations.AddField(
            model_name="simulationrunstate",
            name="initiated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="qbet_simulation_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
