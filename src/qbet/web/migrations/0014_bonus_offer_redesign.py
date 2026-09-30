from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_promotion_shapes(apps, schema_editor) -> None:  # noqa: ARG001
    BonusOffer = apps.get_model("web", "BonusOffer")
    BonusOffer.objects.filter(
        promotion_shape="",
        promotion_type="qualifying_bet",
    ).update(promotion_shape="qualifying_stage")
    BonusOffer.objects.filter(
        promotion_shape="",
        promotion_type="free_bet",
    ).update(promotion_shape="free_bet")


def harden_bonus_offer_revision_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    table = "qbet_bonus_offer_revisions"
    schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    schema_editor.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM anon';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM authenticated';
            END IF;
        END
        $$;
        """
    )


def unharden_bonus_offer_revision_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            'ALTER TABLE "qbet_bonus_offer_revisions" DISABLE ROW LEVEL SECURITY'
        )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("web", "0013_provider_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="bonusoffer",
            name="promotion_shape",
            field=models.CharField(
                blank=True,
                choices=[
                    ("bet_and_get", "Bet & get a free bet"),
                    ("free_bet", "Free bet already available"),
                    ("other", "Other / unsupported promotion"),
                    ("qualifying_stage", "Qualifying wager stage (legacy)"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="bonusoffer",
            name="promotion_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("qualifying_bet", "Qualifying bet"),
                    ("free_bet", "Free bet"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="bonusoffer",
            name="unsupported_terms",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(backfill_promotion_shapes, migrations.RunPython.noop),
        migrations.CreateModel(
            name="BonusOfferRevision",
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
                ("snapshot", models.JSONField()),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="qbet_bonus_offer_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "offer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="web.bonusoffer",
                    ),
                ),
            ],
            options={
                "db_table": "qbet_bonus_offer_revisions",
                "ordering": ("-changed_at", "-id"),
            },
        ),
        migrations.RunPython(
            harden_bonus_offer_revision_table,
            unharden_bonus_offer_revision_table,
        ),
    ]
