from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def seed_catalog(apps, schema_editor) -> None:  # noqa: ARG001
    from qbet.providers import load_german_sportsbook_catalog

    Provider = apps.get_model("storage", "SportsbookProviderRow")
    Domain = apps.get_model("storage", "SportsbookProviderDomainRow")
    Identity = apps.get_model("storage", "SportsbookExternalIdentityRow")
    catalog = load_german_sportsbook_catalog()
    for provider in catalog.providers:
        Provider.objects.update_or_create(
            provider_id=provider.provider_id,
            defaults={
                "legal_name": provider.legal_name,
                "display_name": provider.display_name,
                "jurisdiction": provider.jurisdiction,
                "sports_betting": provider.sports_betting,
                "online": provider.online,
                "source_url": provider.source_url,
                "whitelist_snapshot_date": provider.whitelist_snapshot_date,
                "status": provider.status.value,
            },
        )
        for domain in provider.domains:
            Domain.objects.update_or_create(
                domain=domain,
                defaults={"provider_id": provider.provider_id},
            )
    for mapping in catalog.mappings:
        Identity.objects.update_or_create(
            source_id=mapping.source_id,
            external_key=mapping.external_key,
            defaults={"provider_id": mapping.provider_id},
        )


_TABLES = (
    "qbet_sportsbook_providers",
    "qbet_sportsbook_provider_domains",
    "qbet_sportsbook_external_identities",
)


def harden_catalog_tables(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        schema_editor.execute(
            f"""
            DO $
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM anon';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM authenticated';
                END IF;
            END
            $;
            """
        )


def unharden_catalog_tables(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("storage", "0012_harden_operational_rls")]

    operations = [
        migrations.CreateModel(
            name="SportsbookProviderRow",
            fields=[
                ("provider_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("legal_name", models.CharField(max_length=255)),
                ("display_name", models.CharField(max_length=255)),
                ("jurisdiction", models.CharField(max_length=2)),
                ("sports_betting", models.BooleanField()),
                ("online", models.BooleanField()),
                ("source_url", models.URLField(max_length=500)),
                ("whitelist_snapshot_date", models.DateField()),
                ("status", models.CharField(max_length=32)),
            ],
            options={"db_table": "qbet_sportsbook_providers"},
        ),
        migrations.CreateModel(
            name="SportsbookProviderDomainRow",
            fields=[
                ("domain", models.CharField(max_length=255, primary_key=True, serialize=False)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domains",
                        to="storage.sportsbookproviderrow",
                    ),
                ),
            ],
            options={"db_table": "qbet_sportsbook_provider_domains"},
        ),
        migrations.CreateModel(
            name="SportsbookExternalIdentityRow",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("source_id", models.CharField(max_length=255)),
                ("external_key", models.CharField(max_length=255)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_identities",
                        to="storage.sportsbookproviderrow",
                    ),
                ),
            ],
            options={"db_table": "qbet_sportsbook_external_identities"},
        ),
        migrations.AddConstraint(
            model_name="sportsbookexternalidentityrow",
            constraint=models.UniqueConstraint(
                fields=("source_id", "external_key"),
                name="qbet_sportsbook_external_identity_unique",
            ),
        ),
        migrations.RunPython(seed_catalog, migrations.RunPython.noop),
        migrations.RunPython(harden_catalog_tables, unharden_catalog_tables),
    ]
