from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def harden_provider_account_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    table = "qbet_provider_accounts"
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


def unharden_provider_account_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute('ALTER TABLE "qbet_provider_accounts" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0013_sportsbook_provider_catalog"),
        ("web", "0012_user_avatar"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderAccount",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("declared", "Declared"),
                            ("active", "Active"),
                            ("unavailable", "Unavailable"),
                            ("frozen", "Frozen"),
                            ("needs_verification", "Needs Verification"),
                        ],
                        default="declared",
                        max_length=32,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("api", "Api"),
                            ("browser", "Browser"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("nickname", models.CharField(blank=True, default="", max_length=80)),
                ("notes", models.CharField(blank=True, default="", max_length=500)),
                ("configured_at", models.DateTimeField(auto_now_add=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="user_accounts",
                        to="storage.sportsbookproviderrow",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qbet_provider_accounts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "qbet_provider_accounts",
                "ordering": ("provider__display_name", "provider_id"),
            },
        ),
        migrations.AddConstraint(
            model_name="provideraccount",
            constraint=models.UniqueConstraint(
                fields=("user", "provider"),
                name="qbet_provider_account_user_provider_unique",
            ),
        ),
        migrations.RunPython(
            harden_provider_account_table,
            unharden_provider_account_table,
        ),
    ]
