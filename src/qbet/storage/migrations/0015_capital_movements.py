from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0014_polling_work")]

    operations = [
        migrations.CreateModel(
            name="CapitalMovementRow",
            fields=[
                ("movement_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("proposal_id", models.UUIDField(editable=False, unique=True)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("state", models.CharField(db_index=True, max_length=32)),
                ("ledger_applied", models.BooleanField(default=False)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_capital_movements",
                "ordering": ("-updated_at",),
            },
        ),
    ]
