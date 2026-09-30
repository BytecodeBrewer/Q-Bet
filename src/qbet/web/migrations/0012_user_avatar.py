from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def harden_user_avatar_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    table = "qbet_user_avatars"
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


def unharden_user_avatar_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute('ALTER TABLE "qbet_user_avatars" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0011_portfolio_capital_location"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserAvatar",
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
                ("object_key", models.CharField(max_length=255, unique=True)),
                ("content_type", models.CharField(default="image/jpeg", max_length=32)),
                ("byte_size", models.PositiveIntegerField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qbet_avatar",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "qbet_user_avatars"},
        ),
        migrations.RunPython(harden_user_avatar_table, unharden_user_avatar_table),
    ]
