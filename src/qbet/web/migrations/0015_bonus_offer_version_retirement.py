from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0014_bonus_offer_redesign"),
    ]

    operations = [
        migrations.AddField(
            model_name="bonusoffer",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="bonusoffer",
            name="retired_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
